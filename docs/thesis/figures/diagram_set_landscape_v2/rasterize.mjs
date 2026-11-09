import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import fs from 'node:fs/promises';
const require=createRequire(process.env.KLTN_NODE_PACKAGE_ROOT || import.meta.url);
const sharp=require('sharp');
const here=path.dirname(fileURLToPath(import.meta.url));
const manifest=JSON.parse(await fs.readFile(path.join(here,'manifest.json'),'utf8'));
for(const d of manifest.figures){
 const svg=path.join(here,d.stem+'.svg');
 await sharp(svg,{density:144}).png().withMetadata({density:300}).toFile(path.join(here,d.stem+'.png'));
 await sharp(svg).resize({width:1800}).png().toFile(path.join(here,d.stem+'.preview.png'));
 console.log(d.stem+' PNG + preview');
}
