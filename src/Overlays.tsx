import {useEffect,useRef,useState,type ReactNode} from 'react';
import {X,Check,BookmarkSimple,CheckCircle,ArrowRight,Info,Star} from '@phosphor-icons/react';
import type {Shopping} from './useShopping';
import {displayValue,formatPrice,formatRating,humanize,matchLabel,matrixRows,present,signalLabel} from './model';

function Modal({children,onClose,kind,title}:{children:ReactNode;onClose:()=>void;kind:string;title:string}){
  const dialog=useRef<HTMLDialogElement>(null);
  useEffect(()=>{const element=dialog.current;element?.showModal();return()=>{element?.close()}},[]);
  return <dialog ref={dialog} className={`modal ${kind}`} aria-label={title} onCancel={onClose} onClick={e=>{if(e.target===e.currentTarget)onClose()}}><section className="modal-surface"><button className="icon-button close-dialog" aria-label={`Close ${title.toLowerCase()}`} onClick={onClose}><X size={22}/></button>{children}</section></dialog>;
}
export function DetailDrawer({shop}:{shop:Shopping}){
  const p=shop.detail;if(!p)return null;
  const saved=shop.selection.selected_asins.includes(p.parent_asin);
  const disabled=shop.busy||shop.unusable||(!saved&&shop.selection.selection_count>=shop.selection.max_selections);
  return <Modal title="Product details" kind="drawer" onClose={shop.closeDetail}><div className="drawer-scroll"><p className="eyebrow">Product details</p><h2>{p.title}</h2><p className="muted">{p.store||'Catalog product'}{p.categories?.length?` · ${p.categories.at(-1)}`:''}</p><div className="detail-meta"><strong>{formatPrice(p.price)}</strong><span><Star size={15} weight="fill"/>{formatRating(p.average_rating)}{present(p.rating_number)?` (${p.rating_number} ratings)`:''}</span></div><p className="fine-print">Price shown as provided. Currency may be unspecified.</p><div className="detail-match"><CheckCircle size={20}/>{matchLabel(p.requirement_match)}</div>
    {!!p.product_description?.length&&<section className="detail-section"><h3>About this option</h3>{p.product_description.map((t,i)=><p key={i}>{t}</p>)}</section>}
    {!!p.product_bullet_points?.length&&<section className="detail-section"><h3>Catalog highlights</h3><ul>{p.product_bullet_points.map((t,i)=><li key={i}>{t}</li>)}</ul></section>}
    <section className="detail-section"><h3>How it fits your needs</h3>{p.requirement_match?.signals?.length?p.requirement_match.signals.map((s,i)=><div className="detail-signal" key={i}><div><strong>{humanize(s.slot)}: {displayValue(s.value)}</strong><span className={'status-pill '+s.status}>{signalLabel(s.status)}</span></div><p>{s.evidence||'No evidence provided.'}</p></div>):<p className="muted">No requirement evidence provided.</p>}</section>
    {!!p.advice?.cons?.length&&<section className="detail-section"><h3>Worth checking</h3>{p.advice.cons.map((c,i)=><div className="caution" key={i}><Info size={16}/><div>{c.text}{c.evidence&&<small>{c.evidence}</small>}</div></div>)}</section>}
    {!!Object.keys(p.details??{}).length&&<section className="detail-section"><h3>Listed details</h3><dl className="details-list">{Object.entries(p.details??{}).map(([key,value])=><div key={key}><dt>{humanize(key)}</dt><dd>{displayValue(value)}</dd></div>)}</dl></section>}
    <p className="fine-print">{p.source_note||p.advice?.disclaimer||'Catalog evidence does not verify live variants, inventory or fit.'}</p></div><footer className="drawer-footer"><button className="primary" disabled={disabled} onClick={()=>shop.save(p.parent_asin,!saved)}>{saved?<Check size={18}/>:<BookmarkSimple size={18}/>} {saved?'Remove from saved options':'Save this option'}</button></footer></Modal>;
}
export function ComparisonDialog({shop}:{shop:Shopping}){
  const [showUnknown,setShowUnknown]=useState(false);
  const h=shop.comparison;if(!h||!shop.compareOpen)return null;
  const products=h.selected_products??[];
  const all=matrixRows(h);
  const rows=all.filter(row=>showUnknown||products.some(p=>present(row.values[p.parent_asin]?.value)));
  const finalized=shop.selection.finalized;
  return <Modal title="Compare options" kind="comparison-modal" onClose={shop.closeComparison}><header className="comparison-heading"><p className="eyebrow">Your decision, made clearer</p><h2>{finalized?'A choice to feel good about.':'Find the fit that matters.'}</h2><p>{h.comparison_scope==='requested_products'?'Comparing the options requested in your conversation.':'Your saved options, side by side. Review the evidence before you decide.'}</p></header>
    {finalized&&<div className="confirmation-banner"><CheckCircle size={22} weight="fill"/><div><strong>Selection confirmed</strong><span>Your choices are saved in this session. No order has been placed.</span></div></div>}
    <div className="comparison-toolbar"><span><Info size={15}/>{h.comparison_assist?.status==='completed'?'Evidence-based comparison':h.comparison_assist?.status==='partial'?'Some comparison details are unavailable':'Catalog comparison · model analysis unavailable'}</span><label><input type="checkbox" checked={showUnknown} onChange={e=>setShowUnknown(e.target.checked)}/>Show unlisted details</label></div>
    <div className="comparison-table-wrap"><table><caption className="sr-only">Product comparison, with catalog evidence</caption><thead><tr><th scope="col">What matters</th>{products.map((p,i)=><th scope="col" key={p.parent_asin}><span className="table-rank">Option {i+1}</span><strong>{p.title}</strong><span className="table-price">{formatPrice(p.price)}</span></th>)}</tr></thead><tbody>{rows.map((row,i)=><tr key={`${row.dimension}-${i}`}><th scope="row">{humanize(row.dimension)}</th>{products.map(p=>{const cell=row.values[p.parent_asin];return <td key={p.parent_asin}><span className={!present(cell?.value)?'muted':''}>{displayValue(cell?.value)}</span>{present(cell?.evidence)&&<details className="cell-evidence"><summary>View evidence</summary><p>{displayValue(cell.evidence)}</p></details>}</td>})}</tr>)}</tbody></table>{!rows.length&&<p className="comparison-empty">No comparison details are available for these options.</p>}</div>
    <footer className="comparison-footer"><p><Info size={16}/>Confirming saves your decision. You can keep exploring.</p><div><button className="secondary" onClick={shop.closeComparison}>Keep exploring</button><button className="primary" disabled={shop.busy||shop.unusable||shop.selection.selection_count<1||finalized} onClick={()=>shop.send('Finalize my selection.')}>{finalized?<><Check size={18}/>Selection confirmed</>:<>Confirm selection<ArrowRight size={18}/></>}</button></div></footer></Modal>;
}
