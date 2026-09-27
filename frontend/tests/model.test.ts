import { describe, it, expect } from 'vitest';
import { formatPrice, formatRating, requirementGroups, replyOptions, matrixRows, matchLabel } from '../src/model';

describe('nullable catalog data', () => {
  it('does not turn missing prices into zero or add a currency', () => {
    expect(formatPrice(null)).toBe('Price unavailable');
    expect(formatPrice(undefined)).toBe('Price unavailable');
    expect(formatPrice(0)).toBe('0.00');
    expect(formatPrice(39.99)).toBe('39.99');
    expect(formatPrice('$42.00')).toBe('$42.00');
  });
  it('keeps a missing rating distinct from zero', () => {
    expect(formatRating(null)).toBe('No rating');
    expect(formatRating(0)).toBe('0 / 5');
  });
  it('does not call no hard requirements a perfect match', () => {
    expect(matchLabel({hard_total:0,hard_supported:0} as any)).toBe('No hard requirements');
    expect(matchLabel({hard_total:3,hard_supported:2} as any)).toBe('2 of 3 must-haves supported');
  });
});
describe('conditional requirement and reply fields', () => {
  it('handles both scalar and array requirements', () => {
    expect(requirementGroups({color:'blue',material:['cotton','linen']})).toEqual([{slot:'color',value:'blue'},{slot:'material',value:'cotton'},{slot:'material',value:'linen'}]);
  });
  it('shows replies even without a question', () => {
    expect(replyOptions({question:null,suggested_replies:['Keep looking']})).toEqual([{label:'Keep looking',message:'Keep looking'}]);
  });
  it('maps an option label to its actual submission text', () => {
    expect(replyOptions({question:{options:['cotton'],option_labels:{cotton:'Cotton fabric'}}})).toEqual([{label:'Cotton fabric',message:'cotton'}]);
  });
});
describe('comparison data', () => {
  it('uses dynamic rows indexed by product id and preserves unknown cells', () => {
    const rows=matrixRows({comparison_assist:{objective_comparison:{comparison_matrix:[{dimension:'breathability',values:{a:{value:'Listed',evidence:'Cotton'},b:{value:null}}}]}}} as any);
    expect(rows[0].dimension).toBe('breathability');
    expect(rows[0].values.a.value).toBe('Listed');
    expect(rows[0].values.b.value).toBe(null);
  });
  it('falls back to basic comparison when assistance is absent', () => {
    const rows=matrixRows({comparison_summary:{rows:[{parent_asin:'a',price:null,rating:4.5,hard_coverage:0.5}]}} as any);
    expect(rows.find(r=>r.dimension==='Price')?.values.a.value).toBe('Price unavailable');
    expect(rows.find(r=>r.dimension==='Rating')?.values.a.value).toBe('4.5 / 5');
  });
});
