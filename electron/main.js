/**
 * Orbix Laptop App - Electron wrapper with full Python backend
 * All Orbix inside desktop app, offline SQLite, 24/7 collection
 */
const { app, BrowserWindow, Menu, Tray, ipcMain, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');

let mainWindow;
let tray;
let pythonProcess;
let isDev = process.argv.includes('--dev');

const DATA_DIR = path.join(app.getPath('userData'), 'orbix_data');
if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, {recursive: true});

function startPythonBackend() {
  const pythonPath = process.platform === 'win32' ? 'python' : 'python3';
  const apiPath = path.join(__dirname, '..', 'orbix', 'mobile_api.py');
  const webPath = path.join(__dirname, '..', 'web');
  
  // Try mobile API first (SQLite), fallback to main API (DuckDB)
  const backendScript = fs.existsSync(apiPath) ? apiPath : path.join(__dirname, '..', 'orbix', 'api.py');
  
  console.log(`[Orbix Laptop] Starting Python backend: ${backendScript}`);
  console.log(`[Orbix Laptop] Data dir: ${DATA_DIR}`);
  
  pythonProcess = spawn(pythonPath, [
    '-m', 'uvicorn',
    'orbix.mobile_api:mobile_app',
    '--host', '127.0.0.1',
    '--port', '8001',
    '--workers', '1'
  ], {
    cwd: path.join(__dirname, '..'),
    env: {
      ...process.env,
      ORBIX_MOBILE_DATA_DIR: DATA_DIR,
      ORBIX_DATA_DIR: DATA_DIR,
      PYTHONPATH: path.join(__dirname, '..')
    }
  });

  pythonProcess.stdout.on('data', (data) => {
    console.log(`[Python] ${data}`);
  });
  
  pythonProcess.stderr.on('data', (data) => {
    console.error(`[Python Error] ${data}`);
  });

  pythonProcess.on('close', (code) => {
    console.log(`[Orbix Laptop] Python backend exited with ${code}`);
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    backgroundColor: '#050505',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      enableRemoteModule: false
    },
    icon: path.join(__dirname, 'build', 'icon.png')
  });

  // Load local web UI - all Orbix inside app
  const webIndex = path.join(__dirname, '..', 'web', 'index.html');
  const mobileIndex = path.join(__dirname, '..', 'mobile', 'dist', 'index.html');
  
  // Try to load from Python backend first (live), fallback to static file
  const url = isDev ? 'http://127.0.0.1:8001' : `file://${webIndex}`;
  
  // Wait a bit for Python to start
  setTimeout(() => {
    mainWindow.loadURL(url).catch(() => {
      console.log('[Orbix Laptop] Backend not ready, loading static');
      mainWindow.loadFile(webIndex);
    });
  }, isDev ? 500 : 2000);

  if (isDev) {
    mainWindow.webContents.openDevTools();
  }

  mainWindow.on('close', (e) => {
    if (!app.isQuitting) {
      e.preventDefault();
      mainWindow.hide();
      if (process.platform === 'darwin') {
        app.dock.hide();
      }
    }
  });

  // Menu
  const template = [
    {
      label: 'Orbix',
      submenu: [
        {label: 'Dashboard', accelerator: 'CmdOrCtrl+1', click: () => mainWindow.webContents.send('navigate', 'dashboard')},
        {label: 'Engine', accelerator: 'CmdOrCtrl+2', click: () => mainWindow.webContents.send('navigate', 'engine')},
        {label: 'Backtest', accelerator: 'CmdOrCtrl+3', click: () => mainWindow.webContents.send('navigate', 'backtest')},
        {type: 'separator'},
        {label: 'Import CSV', click: async () => {
          const result = await dialog.showOpenDialog({filters: [{name: 'CSV', extensions: ['csv']}]});
          if (!result.canceled) mainWindow.webContents.send('import-file', result.filePaths[0]);
        }},
        {type: 'separator'},
        {role: 'quit'}
      ]
    },
    {
      label: 'View',
      submenu: [
        {role: 'reload'},
        {role: 'toggleDevTools'},
        {type: 'separator'},
        {role: 'resetZoom'},
        {role: 'zoomIn'},
        {role: 'zoomOut'},
        {type: 'separator'},
        {role: 'togglefullscreen'}
      ]
    },
    {
      label: 'Data',
      submenu: [
        {label: 'Open Data Folder', click: () => require('electron').shell.openPath(DATA_DIR)},
        {label: 'Clear Local DB', click: async () => {
          const res = await dialog.showMessageBox({
            type: 'warning',
            buttons: ['Cancel', 'Clear'],
            message: 'Clear all local database? This deletes all candles and signals inside app.'
          });
          if (res.response === 1) {
            mainWindow.webContents.send('clear-db');
          }
        }}
      ]
    }
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function createTray() {
  try {
    tray = new Tray(path.join(__dirname, 'build', 'icon.png'));
    const contextMenu = Menu.buildFromTemplate([
      {label: 'Show Orbix', click: () => { mainWindow.show(); }},
      {label: 'Generate Signal', click: () => { mainWindow.show(); mainWindow.webContents.send('generate-signal'); }},
      {type: 'separator'},
      {label: 'Quit', click: () => { app.isQuitting = true; app.quit(); }}
    ]);
    tray.setToolTip('Orbix Signal Lab - Running 24/7 with local DB');
    tray.setContextMenu(contextMenu);
    tray.on('click', () => mainWindow.show());
  } catch (e) {
    console.log('[Orbix Laptop] Tray failed', e.message);
  }
}

app.whenReady().then(() => {
  startPythonBackend();
  createWindow();
  createTray();
  
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
    else mainWindow.show();
  });
});

app.on('before-quit', () => {
  app.isQuitting = true;
  if (pythonProcess) pythonProcess.kill();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// IPC handlers for mobile<->laptop bridge
ipcMain.handle('get-data-dir', () => DATA_DIR);
ipcMain.handle('get-app-version', () => app.getVersion());
ipcMain.handle('is-packaged', () => app.isPackaged);
