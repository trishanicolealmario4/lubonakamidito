import React, { useState, useEffect } from 'react'
import {
  Box,
  Grid,
  Card,
  CardContent,
  Typography,
  CircularProgress,
  Alert,
  Chip,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
} from '@mui/material'
import WarningIcon from '@mui/icons-material/Warning'
import api from '../services/api'
import { useAuth } from '../contexts/AuthContext'

const DEFAULT_WARNING_MESSAGE = 'An official flood advisory has been issued. Stay alert and follow instructions from local authorities.'

const WaterLevel = () => {
  const { user } = useAuth()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [currentLevel, setCurrentLevel] = useState(null)
  const [manualSending, setManualSending] = useState(false)
  const [manualStatus, setManualStatus] = useState(null)
  const [manualDialogOpen, setManualDialogOpen] = useState(false)
  const [manualSource, setManualSource] = useState('PAGASA')
  const [manualMessage, setManualMessage] = useState(DEFAULT_WARNING_MESSAGE)
  const [broadcastId, setBroadcastId] = useState(null)
  const completeSmsLength = `OFFICIAL FLOOD WARNING FROM ${manualSource.trim()}: ${manualMessage.trim()}`.length

  useEffect(() => {
    fetchCurrentLevel()

    const interval = setInterval(fetchCurrentLevel, 10000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    if (!broadcastId) return undefined

    let cancelled = false
    let timeout
    const pollBroadcast = async () => {
      try {
        const response = await api.get(`/api/sms/manual-broadcasts/${broadcastId}/`)
        if (cancelled) return

        const broadcast = response.data
        if (broadcast.status === 'completed') {
          setManualStatus({
            severity: 'success',
            message: `The modem accepted ${broadcast.sent_count} SMS message(s). Handset delivery is not confirmed.`,
          })
          setBroadcastId(null)
          return
        }
        if (broadcast.status === 'failed') {
          setManualStatus({
            severity: 'error',
            message: `Broadcast finished with ${broadcast.sent_count} accepted and ${broadcast.failed_count} failed. ${broadcast.error_message || ''}`.trim(),
          })
          setBroadcastId(null)
          return
        }
        if (broadcast.status === 'partial') {
          setManualStatus({
            severity: 'warning',
            message: `Partial broadcast: modem accepted ${broadcast.sent_count} SMS message(s); ${broadcast.failed_count} failed. Handset delivery is not confirmed.`,
          })
          setBroadcastId(null)
          return
        }

        setManualStatus({
          severity: 'info',
          message: broadcast.status === 'sending'
            ? 'The bridge is processing this warning for active SMS-enabled residents.'
            : `Warning queued for up to ${broadcast.recipient_count} active SMS-enabled residents. Waiting for the SMS bridge.`,
        })
      } catch (pollError) {
        if (!cancelled) {
          console.error('Manual warning status error:', pollError)
          setManualStatus({
            severity: 'info',
            message: 'Warning is queued. Unable to check its delivery status right now; retrying.',
          })
        }
      }

      if (!cancelled) timeout = setTimeout(pollBroadcast, 3000)
    }

    timeout = setTimeout(pollBroadcast, 1500)
    return () => {
      cancelled = true
      clearTimeout(timeout)
    }
  }, [broadcastId])

  const fetchCurrentLevel = async () => {
    try {
      const response = await api.get('/api/water-level/current/')
      setCurrentLevel(response.data)
      setError(null)
    } catch (err) {
      setError('Failed to fetch current water level')
    } finally {
      setLoading(false)
    }
  }

  const sendManualWarning = async () => {
    setManualSending(true)
    setManualStatus(null)
    try {
      const response = await api.post('/api/alerts/manual-warning/', {
        source: manualSource.trim(),
        message: manualMessage.trim(),
      })
      setBroadcastId(response.data.broadcast_id)
      setManualDialogOpen(false)
      setManualStatus({
        severity: 'info',
        message: `Warning queued for ${response.data.recipient_count} active SMS-enabled resident(s).`,
      })
    } catch (err) {
      const responseData = err.response?.data
      const errorDetails = responseData?.details || responseData
      const firstDetail = errorDetails && typeof errorDetails === 'object'
        ? Object.values(errorDetails).flatMap((value) => Array.isArray(value) ? value : [value]).find((value) => typeof value === 'string')
        : null
      setManualStatus({
        severity: 'error',
        message: firstDetail || responseData?.reason || responseData?.message || 'Failed to queue the manual warning.',
      })
    } finally {
      setManualSending(false)
    }
  }

  const getStatusColor = (status) => {
    switch (status) {
      case 'Normal':
        return 'success'
      case 'Alert':
        return 'info'
      case 'Warning':
        return 'warning'
      case 'Danger':
        return 'error'
      default:
        return 'default'
    }
  }

  // Reflects the sensor only. GSM is reported separately below, and is legitimately
  // disconnected on a USB-tethered setup where the sensor itself is fine.
  const sensorIsOnline = currentLevel?.sensor_status === 'online'
  const isLive = currentLevel?.source === 'firebase_sensor'

  if (loading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
        <CircularProgress />
      </Box>
    )
  }

  return (
    <Box>
      <Typography variant="h4" gutterBottom>
        Water Level Monitoring
      </Typography>
      <Typography variant="body1" color="text.secondary" gutterBottom>
        Live water level reading from the connected GSM sensor
      </Typography>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      <Grid container spacing={3} sx={{ mt: 2 }}>
        {/* Current Water Level Card */}
        <Grid item xs={12}>
          <Card>
            <CardContent>
              <Box display="flex" justifyContent="space-between" alignItems="center">
                <Typography color="textSecondary" gutterBottom variant="h6">
                  Current Water Level
                </Typography>
                <Box display="flex" alignItems="center" gap={1}>
                  <Chip
                    label={sensorIsOnline ? 'SENSOR ONLINE' : 'SENSOR OFFLINE'}
                    color={sensorIsOnline ? 'success' : 'error'}
                    size="small"
                  />
                </Box>
              </Box>
              {currentLevel ? (
                <>
                  <Typography variant="h3" sx={{ mt: 2 }}>
                    {currentLevel.water_level_cm} cm
                  </Typography>
                  <Chip
                    label={currentLevel.status}
                    color={getStatusColor(currentLevel.status)}
                    size="large"
                    sx={{ mt: 2 }}
                  />
                  <Box sx={{ mt: 3 }}>
                    <Typography variant="body2" color="text.secondary">
                      Timestamp: {new Date(currentLevel.timestamp).toLocaleString()}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Sensor Status: {currentLevel.sensor_status}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      GSM Status: {currentLevel.gsm_status}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Source: {isLive ? 'Live sensor via Firebase' : 'Stored database reading'}
                    </Typography>
                  </Box>
                </>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  No current reading available
                </Typography>
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* Manual Citizen Warning */}
        {user?.role === 'admin' && <Grid item xs={12}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Manual SMS Warning to Citizens
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                Send an official advisory by SMS to all active residents who enabled SMS alerts. This can be triggered independently of the water-level sensor status.
              </Typography>
              <Button
                variant="contained"
                color="warning"
                startIcon={<WarningIcon />}
                onClick={() => {
                  setManualStatus(null)
                  setManualDialogOpen(true)
                }}
                disabled={manualSending || Boolean(broadcastId)}
              >
                {manualSending ? 'Queueing Warning...' : broadcastId ? 'Warning In Progress...' : 'Send Warning to Citizens'}
              </Button>
              {manualStatus && (
                <Alert severity={manualStatus.severity} sx={{ mt: 2 }}>
                  {manualStatus.message}
                </Alert>
              )}
            </CardContent>
          </Card>
        </Grid>}

      </Grid>

      <Dialog open={manualDialogOpen} onClose={() => !manualSending && setManualDialogOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>Send an official SMS warning</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <Alert severity="warning">
              This sends to every active resident with SMS enabled, regardless of the sensor reading. The serial bridge and GSM modem must be running.
            </Alert>
            <TextField
              label="Announcement source"
              value={manualSource}
              onChange={(event) => setManualSource(event.target.value)}
              helperText="This source will be included in the SMS, e.g. PAGASA or Barangay Officials."
              inputProps={{ maxLength: 120 }}
              required
              fullWidth
            />
            <TextField
              label="Warning message"
              value={manualMessage}
              onChange={(event) => setManualMessage(event.target.value)}
              inputProps={{ maxLength: 480 }}
              helperText={`${completeSmsLength}/480 SMS characters including source. Use plain English/ASCII text for the GSM modem.`}
              multiline
              minRows={3}
              required
              fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setManualDialogOpen(false)} disabled={manualSending}>Cancel</Button>
          <Button
            variant="contained"
            color="warning"
            startIcon={<WarningIcon />}
            onClick={sendManualWarning}
            disabled={manualSending || !manualSource.trim() || !manualMessage.trim() || completeSmsLength > 480}
          >
            {manualSending ? 'Queueing...' : 'Confirm and send'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

export default WaterLevel
