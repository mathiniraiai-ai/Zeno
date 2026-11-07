const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('zeno', {
  root: () => ipcRenderer.invoke('zeno:root'),
  jobs: () => ipcRenderer.invoke('zeno:jobs'),
  detail: (name, since) => ipcRenderer.invoke('zeno:detail', name, since),
  info: (check) => ipcRenderer.invoke('zeno:info', check),
  reveal: (p) => ipcRenderer.invoke('zeno:reveal', p),
  open: (p) => ipcRenderer.invoke('zeno:open', p),
  newEdit: () => ipcRenderer.invoke('zeno:newEdit'),
  update: () => ipcRenderer.invoke('zeno:update'),
  platform: process.platform,
});
