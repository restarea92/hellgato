import { readFileSync, writeFileSync, readdirSync, realpathSync } from 'node:fs';
import { resolve, relative, join } from 'node:path';
import { inspectFrameAdapter, sha256 } from '../../apps/stream-dog/node/frame-adapter-registry.mjs';
const [metadata, directory] = process.argv.slice(2);
if (!metadata || !directory) throw new Error('Use create-adapter-manifest.mjs METADATA.json PACKAGE_DIRECTORY');
const root=realpathSync(resolve(directory)), value=JSON.parse(readFileSync(resolve(metadata),'utf8')), files={};
function visit(folder){for(const entry of readdirSync(folder,{withFileTypes:true})){
  if(entry.isSymbolicLink()) throw new Error('Adapter packages cannot contain symlinks');
  const path=join(folder,entry.name),name=relative(root,path).replaceAll('\\','/');
  if(name==='adapter.json') continue;
  if(entry.isDirectory()) visit(path);else if(entry.isFile()) files[name]=sha256(readFileSync(path));else throw new Error('Unsupported adapter artifact');
}}
visit(root);const manifestPath=resolve(root,'adapter.json');
writeFileSync(manifestPath,JSON.stringify({...value,schemaVersion:1,kind:'hellgato-frame-adapter',protocolVersion:1,files},null,2)+'\n');
const adapter=inspectFrameAdapter(manifestPath);console.log(JSON.stringify({id:adapter.id,manifest:manifestPath,files:Object.keys(files).length}));
