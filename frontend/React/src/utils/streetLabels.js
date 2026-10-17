export const formatStreetLabel = (value) => (
  String(value ?? '').trim().replace(/^(?:purok|street)\b/i, 'Street')
)
