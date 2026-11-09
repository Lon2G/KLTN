// Export the vector figure without changing its content. Requires sharp.
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const require = createRequire(process.env.KLTN_NODE_PACKAGE_ROOT || import.meta.url);
const sharp = require('sharp');
const here = path.dirname(fileURLToPath(import.meta.url));
const svg = path.join(here, 'graphical_abstract_landscape.en.svg');
await sharp(svg, {density: 144}).png().withMetadata({density: 300}).toFile(path.join(here, 'graphical_abstract_landscape.en.png'));
await sharp(svg).resize({width: 2160}).png().toFile(path.join(here, 'graphical_abstract_landscape.en.preview.png'));
console.log('Exported 5400 x 2920 PNG (300 dpi) and 2160 px preview.');
