// Renders tools/icon.html to the app icon set: node tools/make-icon.mjs
// Uses Playwright from the reels project (no install needed here).
import { chromium } from '/Users/gizmo/Desktop/niva-showreel/node_modules/playwright/index.mjs';
import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const set = path.join(root, 'dots/Assets.xcassets/AppIcon.appiconset');
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1024, height: 1024 } });
await page.goto(pathToFileURL(path.join(root, 'tools/icon.html')).href);
await page.waitForFunction(() => window.READY);
const master = path.join(set, 'icon_1024.png');
await page.locator('#c').screenshot({ path: master, omitBackground: true });
await browser.close();

const images = [];
for (const size of [16, 32, 128, 256, 512]) {
  for (const scale of [1, 2]) {
    const px = size * scale;
    const name = `icon_${size}x${size}${scale === 2 ? '@2x' : ''}.png`;
    execFileSync('sips', ['-z', String(px), String(px), master, '--out', path.join(set, name)], { stdio: 'ignore' });
    images.push({ idiom: 'mac', scale: `${scale}x`, size: `${size}x${size}`, filename: name });
  }
}
execFileSync('rm', [master]);
writeFileSync(path.join(set, 'Contents.json'), JSON.stringify({ images, info: { author: 'xcode', version: 1 } }, null, 2));
console.log('icon set written');
