/** Only the pinned vendor constructors used by typed target glue are exposed. */
declare module '*vendor/cora/primary-server.js' {
  export const ElgatoServer: import('../src/hosts/streamdeck/target.mjs').PrimaryConstructor;
}
declare module '*vendor/cora/child-server.js' {
  export const ElgatoChildServer: import('../src/hosts/streamdeck/target.mjs').ChildConstructor;
}
