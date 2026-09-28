import { readPrivacyMode, PRIVACY_STORAGE_KEY } from '../../static/js/privacy.js';

// ---------------------------------------------------------------------------
// /privacy is remembered per browser in localStorage. The storage accessor can
// be missing or throw (private window, blocked site data), and that must read as
// "off" rather than breaking page start-up.
// ---------------------------------------------------------------------------

const storeWith = value => ({ getItem: key => (key === PRIVACY_STORAGE_KEY ? value : null) });

describe('readPrivacyMode', () => {
  test("'1' is on", () => {
    expect(readPrivacyMode(storeWith('1'))).toBe(true);
  });

  test("'0' and unset are off", () => {
    expect(readPrivacyMode(storeWith('0'))).toBe(false);
    expect(readPrivacyMode(storeWith(null))).toBe(false);
  });

  test('no storage is off', () => {
    expect(readPrivacyMode(null)).toBe(false);
  });

  test('a throwing accessor is off', () => {
    const blocked = { getItem: () => { throw new Error('SecurityError'); } };
    expect(readPrivacyMode(blocked)).toBe(false);
  });
});
