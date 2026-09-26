import type { CatalogValue, Requirements, Receipt, Handoff, Match, MatrixRow } from './contracts';

export const present = (value: unknown) => value !== null && value !== undefined && value !== '';
export function displayValue(value: unknown): string {
  if (!present(value)) return 'Unknown';
  if (Array.isArray(value)) return value.map(displayValue).join(', ');
  if (typeof value === 'object') return Object.entries(value as object).map(([k,v]) => `${humanize(k)}: ${displayValue(v)}`).join('; ');
  return String(value);
}
export const humanize = (s:string) => s.replaceAll('_',' ').replace(/^./, c=>c.toUpperCase());
export function formatPrice(value:CatalogValue):string {
  if (!present(value) || (typeof value==='number' && !Number.isFinite(value))) return 'Price unavailable';
  return typeof value==='number' ? value.toFixed(2) : String(value);
}
export const formatRating=(value:CatalogValue):string=>present(value) ? `${value} / 5` : 'No rating';
export const requirementGroups=(value:Requirements|undefined):{slot:string;value:string}[]=>Object.entries(value??{}).flatMap(([slot, values])=>(Array.isArray(values)?values:[values]).filter(present).map(v=>({slot,value:String(v)})));
export function replyOptions(receipt:Receipt):{label:string;message:string}[] {
  const followup=receipt.detail_question||receipt.comparison_reference_question||receipt.preference_comparison?.question;
  const q=followup && Array.isArray(receipt.suggested_replies) ? null : receipt.question;
  const choices=q?.correction ? ['Replace','Keep both','Keep original'] : q?.options ?? receipt.suggested_replies ?? [];
  return [...new Set(choices)].filter(v=>typeof v==='string').slice(0,4).map(v=>({label:q?.option_labels?.[v]??v,message:q?.target_slot==='category'?(q.option_labels?.[v]??v):v}));
}
export function matrixRows(handoff:Handoff):MatrixRow[] {
  const enhanced=handoff.comparison_assist?.objective_comparison?.comparison_matrix;
  if(enhanced?.length) return enhanced;
  const entries=handoff.comparison_summary?.rows??[];
  return [
    {dimension:'Price',values:Object.fromEntries(entries.map(p=>[p.parent_asin,{value:formatPrice(p.price)}]))},
    {dimension:'Rating',values:Object.fromEntries(entries.map(p=>[p.parent_asin,{value:formatRating(p.rating)}]))},
    {dimension:'Requirement coverage',values:Object.fromEntries(entries.map(p=>[p.parent_asin,{value:p.hard_coverage==null?null:`${Math.round(p.hard_coverage*100)}% of must-haves`}]))},
  ];
}
export const matchLabel=(value:Match|undefined):string=>!value||!value.hard_total?'No hard requirements':`${value.hard_supported} of ${value.hard_total} must-haves supported`;
export const signalLabel=(status:string)=>({supported:'Supported',not_evidenced:'Not evidenced',unknown:'Unknown',conflict:'Conflicting evidence',clear:'No exclusion found'}[status]??'Unknown');
