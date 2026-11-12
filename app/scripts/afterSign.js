const { execFileSync } = require('child_process');
const path = require('path');

exports.default = async function afterSign(context) {
  if (context.electronPlatformName !== 'darwin') return;

  const appName = context.packager.appInfo.productFilename;
  const appPath = path.join(context.appOutDir, `${appName}.app`);

  // Strip extended attributes / Finder detritus before signing so codesign resource seals never break
  execFileSync('/usr/bin/xattr', ['-cr', appPath], { stdio: 'inherit' });

  // Deep ad-hoc sign all nested helpers, frameworks, and main Mach-O bundle if not signed with an Apple Developer ID
  let hasDeveloperId = false;
  try {
    const info = execFileSync('/usr/bin/codesign', ['-dvv', appPath], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
    hasDeveloperId = /Authority=Developer ID Application/i.test(info);
  } catch (err) {
    const stderr = String((err && err.stderr) || '');
    hasDeveloperId = /Authority=Developer ID Application/i.test(stderr);
  }

  if (!hasDeveloperId) {
    console.log(`  • ad-hoc signing macOS bundle (prevents "damaged" error on Apple Silicon)  app=${appPath}`);
    execFileSync('/usr/bin/codesign', ['--force', '--deep', '--sign', '-', appPath], { stdio: 'inherit' });
  }

  // Hard verification gate: fail packaging immediately if any nested resource or Mach-O seal is invalid
  execFileSync('/usr/bin/codesign', ['--verify', '--deep', '--strict', '--verbose=2', appPath], { stdio: 'inherit' });
  console.log(`  • verified macOS bundle signature OK  app=${appPath}`);
};
