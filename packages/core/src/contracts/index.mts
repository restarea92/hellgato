import canonicalSchema from '../../schemas/contracts.schema.json' with { type: 'json' };

export type CapabilityStatus = 'native' | 'emulated' | 'unsupported' | 'unverified';
export interface Capability { status: CapabilityStatus; limitations?: string[] }
export interface DisplaySurface { id: string; width: number; height: number; format: 'jpeg' | 'rgb888'; partial: boolean }
export interface DeviceCapabilities {
  keys: number;
  dials: number;
  touch: { width: number; height: number } | null;
  displays: DisplaySurface[];
  dialPress: 'native' | 'pulse' | 'unsupported' | 'unverified';
  dialRelease: 'native' | 'unavailable' | 'unverified';
  features?: Record<string, Capability>;
}
export interface AdapterDescriptor {
  id: string;
  version: string;
  modelIds: string[];
  platforms: string[];
  architectures: string[];
  apiVersion: 1;
  protocol?: { major: number; minor: number };
}
export interface DeviceDescriptor {
  id: string;
  adapterId: string;
  modelId: string;
  firmware?: string;
  displayName?: string;
  capabilities: DeviceCapabilities;
}
export interface Geometry {
  rows: number; columns: number; keyCount: number; keyWidth: number; keyHeight: number;
  encoderCount: number; touchWidth: number; touchHeight: number; productName: string;
}
export interface DeviceConfig {
  serialNumber: string; childSerialNumber: string; dockFirmwareVersion: string;
  childFirmwareVersion: string; productId: number; macAddress: number[];
}
export interface HostTarget { id: string; geometry: Geometry; config: DeviceConfig; displays?: DisplaySurface[] }
export interface MappingProfile {
  id: string; version: string; target: HostTarget;
  controls: { keys: Array<number | null>; dials: Array<number | null> };
  requirements: { hostPreparation?: string[]; capabilities?: string[] };
  touch?: { rotation?: 0 | 90 | 180 | 270 };
  displays?: Array<{ source: string; target: string }>;
}
export interface InputEnvelope { deviceId: string; sessionId?: string; generation: number; sequence: number }
export type InputEvent = InputEnvelope & (
  { type: 'key'; index: number; state: 'down' | 'up' } |
  { type: 'dial'; kind: 'rotate'; index: number; delta: number } |
  { type: 'dial'; kind: 'press'; index: number; state: 'down' | 'up' } |
  { type: 'dial'; kind: 'pulse'; index: number } |
  { type: 'touch'; gesture: 'tap' | 'hold'; x: number; y: number } |
  { type: 'touch'; gesture: 'swipe'; x: number; y: number; endX: number; endY: number }
);
export interface OutputEnvelope { deviceId?: string; sessionId?: string; generation?: number; sequence?: number }
export interface DisplayFrame extends OutputEnvelope {
  type: 'frame'; surfaceId: string; format: 'jpeg' | 'rgb888'; width: number; height: number;
  /** Strict padded base64, at most MAX_FRAME_BYTES decoded bytes. */
  data: string;
  region?: { x: number; y: number; width: number; height: number };
}
export type ControlCommand = OutputEnvelope & { type: 'control'; surfaceId?: string } & (
  { control: 'brightness' | 'sleepDuration' | 'knobBrightness'; value: number } |
  { control: 'followDisplayBrightness'; value: boolean } |
  { control: 'clear' | 'sleep' | 'wake' | 'showLogo' | 'screensaver' } |
  { control: 'fill'; color: [number, number, number] }
);
export type OutputEvent = DisplayFrame | ControlCommand;

interface SchemaRule {
  $ref?: string; type?: string; const?: unknown; enum?: unknown[];
  oneOf?: SchemaRule[]; anyOf?: SchemaRule[];
  minimum?: number; maximum?: number; minLength?: number; maxLength?: number; pattern?: string;
  minItems?: number; maxItems?: number; items?: SchemaRule;
  properties?: Record<string, SchemaRule | undefined>; required?: string[];
  additionalProperties?: boolean | SchemaRule; maxProperties?: number;
}
const schema: { $defs: Record<string, SchemaRule> } = canonicalSchema;

export const CONTRACT_VERSION = 1;
export const MAX_FRAME_BYTES = 1024 * 1024;
export const MAX_DIMENSION = 8192;
export const MAX_PIXELS = 16 * 1024 * 1024;

export class ContractError extends TypeError {
  readonly code = 'ERR_CONTRACT';
  readonly path: string;
  constructor(message: string, path = '$') {
    super(`${path}: ${message}`);
    this.name = 'ContractError';
    this.path = path;
  }
}
function fail(message: string, path = '$'): never { throw new ContractError(message, path); };

const schemaKeywords = new Set(['$ref', 'type', 'const', 'enum', 'oneOf', 'anyOf', 'minimum', 'maximum', 'minLength', 'maxLength', 'pattern', 'minItems', 'maxItems', 'items', 'properties', 'required', 'additionalProperties', 'maxProperties']);
const schemaTypes = new Set(['null', 'boolean', 'integer', 'string', 'array', 'object']);
function auditSchema(rule: SchemaRule | undefined): void {
  if (!rule || typeof rule !== 'object' || Array.isArray(rule)) throw new Error('Invalid contract schema rule');
  for (const keyword of Object.keys(rule)) if (!schemaKeywords.has(keyword)) throw new Error(`Unsupported contract schema keyword: ${keyword}`);
  if (rule.type !== undefined && !schemaTypes.has(rule.type)) throw new Error(`Unsupported contract schema type: ${rule.type}`);
  if (rule.$ref !== undefined && (!rule.$ref.startsWith('#/$defs/') || !Object.hasOwn(schema.$defs, rule.$ref.slice('#/$defs/'.length)))) throw new Error(`Unknown contract schema reference: ${rule.$ref}`);
  for (const child of [...Object.values(rule.properties ?? {}), ...(rule.oneOf ?? []), ...(rule.anyOf ?? [])]) auditSchema(child);
  if (rule.items) auditSchema(rule.items);
  if (typeof rule.additionalProperties === 'object') auditSchema(rule.additionalProperties);
}
function freezeSchema(value: unknown): void {
  if (value && typeof value === 'object') { for (const child of Object.values(value)) freezeSchema(child); Object.freeze(value); }
}
for (const rule of Object.values(schema.$defs)) auditSchema(rule);
freezeSchema(schema);

// This deliberately small evaluator implements every keyword used by the
// canonical schema. No executable validation logic is embedded in profiles.
function check(value: unknown, rule: SchemaRule, path: string): void {
  if (rule.$ref) return check(value, schema.$defs[rule.$ref.slice('#/$defs/'.length)], path);
  if (rule.oneOf || rule.anyOf) {
    let matches = 0;
    for (const variant of rule.oneOf ?? rule.anyOf ?? []) {
      try { check(value, variant, path); matches++; } catch (error) { if (!(error instanceof ContractError)) throw error; }
    }
    if (rule.oneOf ? matches !== 1 : matches === 0) fail('does not match a permitted payload', path);
    return;
  }
  if (Object.hasOwn(rule, 'const') && value !== rule.const) fail(`must equal ${rule.const}`, path);
  if (rule.enum && !rule.enum.includes(value)) fail(`must be one of ${rule.enum.join(', ')}`, path);
  if (rule.type === 'null' && value !== null) fail('must be null', path);
  if (rule.type === 'boolean' && typeof value !== 'boolean') fail('must be boolean', path);
  if (rule.type === 'integer' && !Number.isSafeInteger(value)) fail('must be a safe integer', path);
  if (rule.minimum !== undefined && (typeof value !== 'number' || value < rule.minimum)) fail(`must be at least ${rule.minimum}`, path);
  if (rule.maximum !== undefined && (typeof value !== 'number' || value > rule.maximum)) fail(`must be at most ${rule.maximum}`, path);
  if (rule.type === 'string') {
    if (typeof value !== 'string') fail('must be a string', path);
    if (value.length < (rule.minLength ?? 0) || value.length > (rule.maxLength ?? Infinity)) fail('string length is out of bounds', path);
    if (rule.pattern && !new RegExp(rule.pattern).test(value)) fail('string has an invalid format', path);
  }
  if (rule.type === 'array') {
    if (!Array.isArray(value)) fail('must be an array', path);
    if (value.length < (rule.minItems ?? 0) || value.length > (rule.maxItems ?? Infinity)) fail('array length is out of bounds', path);
    for (let index = 0; index < value.length; index++) check(value[index], rule.items ?? {}, `${path}[${index}]`);
  }
  if (rule.type === 'object') {
    if (value === null || typeof value !== 'object' || Array.isArray(value) || ![Object.prototype, null].includes(Object.getPrototypeOf(value))) fail('must be a plain object', path);
    const record = value as Record<string, unknown>;
    const keys = Object.keys(record);
    if (keys.length > (rule.maxProperties ?? Infinity)) fail('too many properties', path);
    for (const name of rule.required ?? []) if (!Object.hasOwn(value, name)) fail(`missing ${name}`, path);
    for (const name of keys) {
      if (Object.hasOwn(rule.properties ?? {}, name)) check(record[name], rule.properties![name]!, `${path}.${name}`);
      else if (rule.additionalProperties === false) fail(`unknown property ${name}`, path);
      else if (typeof rule.additionalProperties === 'object') check(record[name], rule.additionalProperties, `${path}.${name}`);
    }
  }
}

export function validateContract(name: string, value: unknown): unknown {
  if (!Object.hasOwn(schema.$defs, name)) fail(`unknown contract ${name}`);
  check(value, schema.$defs[name], '$');
  return value;
}
function validated<T>(name: string, value: unknown): T { validateContract(name, value); return value as T; }
export function validateAdapterDescriptor(value: unknown): AdapterDescriptor { return validated<AdapterDescriptor>('AdapterDescriptor', value); }
export function validateDeviceCapabilities(input: unknown): DeviceCapabilities {
  const value = validated<DeviceCapabilities>('DeviceCapabilities', input);
  if (new Set(value.displays.map(display => display.id)).size !== value.displays.length) fail('display IDs must be unique', '$.displays');
  for (const display of value.displays) if (display.width * display.height > MAX_PIXELS) fail('surface pixel limit exceeded', '$.displays');
  if (value.dials === 0 && (value.dialPress === 'native' || value.dialPress === 'pulse' || value.dialRelease === 'native')) fail('dial capability requires a dial', '$.dials');
  if (value.dialPress === 'pulse' && value.dialRelease !== 'unavailable') fail('pulse input cannot supply physical release', '$.dialRelease');
  return value;
}
export function validateDeviceDescriptor(input: unknown): DeviceDescriptor {
  const value = validated<DeviceDescriptor>('DeviceDescriptor', input);
  validateDeviceCapabilities(value.capabilities);
  return value;
}
export function validateHostTarget(value: unknown): HostTarget { return validated<HostTarget>('HostTarget', value); }
export function validateMappingProfile(input: unknown): MappingProfile {
  const value = validated<MappingProfile>('MappingProfile', input);
  for (const [kind, limit] of [['keys', value.target.geometry.keyCount], ['dials', value.target.geometry.encoderCount]] as const) {
    const targets = value.controls[kind].filter(index => index !== null);
    if (targets.some(index => index >= limit)) fail('mapping exceeds target controls', `$.controls.${kind}`);
    if (new Set(targets).size !== targets.length) fail('duplicate target mapping', `$.controls.${kind}`);
  }
  return value;
}
export function validateInputEvent(value: unknown): InputEvent { return validated<InputEvent>('InputEvent', value); }
// Header inspection limits compressed frames before a physical adapter decoder.
// Adapters must still fully decode and verify the image before native USB output.
function jpegDimensions(data: Buffer): { width: number; height: number } {
  if (data.length < 4 || data[0] !== 0xff || data[1] !== 0xd8 || data.at(-2) !== 0xff || data.at(-1) !== 0xd9) fail('invalid JPEG framing', '$.data');
  let offset = 2;
  while (offset + 1 < data.length) {
    if (data[offset++] !== 0xff) fail('invalid JPEG marker', '$.data');
    while (data[offset] === 0xff) offset++;
    const marker = data[offset++];
    if (marker === 0xd9 || marker === 0xda) break;
    if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
    if (offset + 2 > data.length) break;
    const length = data.readUInt16BE(offset);
    if (length < 2 || offset + length > data.length) fail('truncated JPEG segment', '$.data');
    if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker)) {
      if (length < 8 || data[offset + 7] < 1 || data[offset + 7] > 4 || length !== 8 + 3 * data[offset + 7]) fail('invalid JPEG dimensions', '$.data');
      const height = data.readUInt16BE(offset + 3);
      const width = data.readUInt16BE(offset + 5);
      if (!width || !height || width > MAX_DIMENSION || height > MAX_DIMENSION || width * height > MAX_PIXELS) fail('JPEG pixel limit exceeded', '$.data');
      return { width, height };
    }
    offset += length;
  }
  fail('JPEG has no dimension header', '$.data');
}
export function validateDisplayFrame(input: unknown): DisplayFrame {
  const value = validated<DisplayFrame>('DisplayFrame', input);
  if (value.width * value.height > MAX_PIXELS) fail('surface pixel limit exceeded', '$');
  const region = value.region ?? { x: 0, y: 0, width: value.width, height: value.height };
  if (region.x + region.width > value.width || region.y + region.height > value.height) fail('region exceeds surface bounds', '$.region');
  const data = Buffer.from(value.data, 'base64');
  if (data.length === 0 || data.length > MAX_FRAME_BYTES || data.toString('base64') !== value.data) fail('invalid or oversized frame bytes', '$.data');
  if (value.format === 'jpeg') {
    const dimensions = jpegDimensions(data);
    if (dimensions.width !== region.width || dimensions.height !== region.height) fail('JPEG dimensions differ from frame/region', '$.data');
  }
  if (value.format === 'rgb888' && data.length !== region.width * region.height * 3) fail('rgb888 length differs from dimensions', '$.data');
  return value;
}
export function validateControlCommand(value: unknown): ControlCommand { return validated<ControlCommand>('ControlCommand', value); }
export const assertAdapterDescriptor = validateAdapterDescriptor;
export const assertDeviceDescriptor = validateDeviceDescriptor;
export const assertDeviceCapabilities = validateDeviceCapabilities;
export const assertHostTarget = validateHostTarget;
export const assertMappingProfile = validateMappingProfile;
export const assertInputEvent = validateInputEvent;
export const assertDisplayFrame = validateDisplayFrame;
export const assertControlCommand = validateControlCommand;
