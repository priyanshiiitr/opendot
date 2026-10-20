const { app, BrowserWindow } = require('electron')
const path = require('path')
function createWindow() {
  const window = new BrowserWindow({ width: 1280, height: 820, minWidth: 900, minHeight: 600, webPreferences: { contextIsolation: true, sandbox: true } })
  const url = process.env.ELECTRON_START_URL || 'http://localhost:5173'
  window.loadURL(url)
}
app.whenReady().then(() => { createWindow(); app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow() }) })
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit() })
