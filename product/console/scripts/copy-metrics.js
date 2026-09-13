const fs = require('fs');
const path = require('path');

const src = path.resolve(__dirname, '../../ml/data/metrics.json');
const destDir = path.resolve(__dirname, '../src/views/exec');
const dest = path.join(destDir, 'metrics.json');

try {
  if (!fs.existsSync(destDir)) fs.mkdirSync(destDir, { recursive: true });
  if (fs.existsSync(src)) {
    fs.copyFileSync(src, dest);
    console.log('Copied metrics.json from', src, 'to', dest);
  } else {
    console.log('Source metrics.json not found at', src, '- leaving existing copy if present.');
  }
} catch (err) {
  console.error('Failed to copy metrics.json:', err);
  process.exitCode = 0; // don't fail the build — best-effort copy
}

