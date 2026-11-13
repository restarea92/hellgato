# Public website

Source for [hellgato-streamdog.vercel.app](https://hellgato-streamdog.vercel.app). Keep the existing light palette and pixel mascots; download links point directly to versioned GitHub releases, with no mirrored installers.

From the repository root:

```sh
node scripts/sync-release-metadata.mjs
node apps/site/build.mjs
```

`release.json` is generated from the private Windows version marker. Deploy the static files in `dist` to the existing Vercel project **hellgato-streamdog**, verify a preview, then promote it to production. No package installation is required. For a Git-based Vercel build, use the repository root and `node apps/site/build.mjs`, with output directory `apps/site/dist`.
