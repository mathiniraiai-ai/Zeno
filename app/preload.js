const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('zeno', {
  info: (check) => ipcRenderer.invoke('zeno:info', check),
  openExternal: (u) => ipcRenderer.invoke('zeno:openExternal', u),
  update: () => ipcRenderer.invoke('zeno:update'),
  reload: () => ipcRenderer.invoke('zeno:reload'),
  signIn: () => ipcRenderer.invoke('zeno:signIn'),
  runSetup: () => ipcRenderer.invoke('zeno:runSetup'),
  platform: process.platform,
});
