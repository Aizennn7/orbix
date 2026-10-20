/**
 * Orbix Laptop Preload - Secure bridge between web and Electron
 */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('OrbixLaptop', {
  isLaptop: true,
  isElectron: true,
  platform: process.platform,
  
  getDataDir: () => ipcRenderer.invoke('get-data-dir'),
  getVersion: () => ipcRenderer.invoke('get-app-version'),
  isPackaged: () => ipcRenderer.invoke('is-packaged'),
  
  onNavigate: (callback) => ipcRenderer.on('navigate', (_, page) => callback(page)),
  onImportFile: (callback) => ipcRenderer.on('import-file', (_, file) => callback(file)),
  onClearDb: (callback) => ipcRenderer.on('clear-db', () => callback()),
  onGenerateSignal: (callback) => ipcRenderer.on('generate-signal', () => callback()),
  
  // For future Supabase sync
  openExternal: (url) => ipcRenderer.invoke('open-external', url)
});

console.log('[Orbix Laptop] Preload ready - all Orbix inside desktop app');
