const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('zeno', {
  root: () => ipcRenderer.invoke('zeno:root'),
  info: (check) => ipcRenderer.invoke('zeno:info', check),
  reveal: (p) => ipcRenderer.invoke('zeno:reveal', p),
  open: (p) => ipcRenderer.invoke('zeno:open', p),
  openExternal: (u) => ipcRenderer.invoke('zeno:openExternal', u),
  newEdit: () => ipcRenderer.invoke('zeno:newEdit'),
  render: (name, planFile) => ipcRenderer.invoke('zeno:render', name, planFile),
  download: (p) => ipcRenderer.invoke('zeno:download', p),
  pickFolder: () => ipcRenderer.invoke('zeno:pickFolder'),
  copy: (t) => ipcRenderer.invoke('zeno:copy', t),
  update: () => ipcRenderer.invoke('zeno:update'),
  reload: () => ipcRenderer.invoke('zeno:reload'),
  signIn: () => ipcRenderer.invoke('zeno:signIn'),
  signOut: () => ipcRenderer.invoke('zeno:signOut'),
  runSetup: () => ipcRenderer.invoke('zeno:runSetup'),
  platform: process.platform,
});
