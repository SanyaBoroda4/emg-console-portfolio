// Google Drive URL helpers. photo_drive_url comes in two shapes in this data:
//   https://drive.google.com/uc?id=<ID>&export=download
//   https://drive.google.com/file/d/<ID>/view?usp=drivesdk

export function extractDriveFileId(url: string): string | null {
  const ucMatch = url.match(/[?&]id=([A-Za-z0-9_-]+)/)
  if (ucMatch) return ucMatch[1]
  const fileMatch = url.match(/\/file\/d\/([A-Za-z0-9_-]+)/)
  if (fileMatch) return fileMatch[1]
  return null
}

export function driveThumbUrl(id: string, width: number): string {
  return `https://drive.google.com/thumbnail?id=${id}&sz=w${width}`
}
