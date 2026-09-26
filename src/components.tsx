import {useEffect,useRef,useState} from 'react';
import {ArrowRight,ArrowUp,ArrowCounterClockwise,ArrowClockwise,BookmarkSimple,Check,CheckCircle,EyeSlash,Sparkle,Star,User,X,Plus,CaretDown,Info,ChatCircle,SlidersHorizontal} from '@phosphor-icons/react';
import type {ProductCard,Requirements} from './contracts';
import type {Shopping} from './useShopping';
import {formatPrice,formatRating,humanize,matchLabel,present,replyOptions,requirementGroups} from './model';

export function ChatPanel({shop}:{shop:Shopping}){
  const [draft,setDraft]=useState('');
  const composing=useRef(false);const end=useRef<HTMLDivElement>(null);const input=useRef<HTMLTextAreaElement>(null);
  const disabled=shop.busy||!shop.session||shop.unusable;
  useEffect(()=>{end.current?.scrollIntoView({block:'nearest',behavior:'smooth'})},[shop.messages,shop.busy]);
  async function submit(){if(disabled||composing.current)return;const value=draft;if(await shop.send(value)){setDraft('');input.current?.focus()}}
  const replies=replyOptions(shop.receipt);
  return <section className="chat-panel" aria-label="Chat">
    <header className="panel-heading"><h1>Let’s find your fit.</h1><p>Tell me what matters to you.</p></header>
    <div className="conversation" aria-live="polite" aria-relevant="additions">
      {!shop.messages.length&&<div className="welcome"><div className="avatar assistant-avatar"><Sparkle size={22} weight="fill"/></div><h3>A little guidance.<br/>A choice that feels right.</h3><p>Describe what you’re looking for. You can add preferences or change your mind anytime.</p><button className="suggestion" disabled={disabled} onClick={()=>setDraft('I need a blue cotton dress.')}>I need a blue cotton dress.<ArrowRight size={16}/></button></div>}
      {shop.messages.map(message=><article className={'message '+message.role} key={message.id}>
        <div className={'avatar '+(message.role==='assistant'?'assistant-avatar':'')} aria-hidden="true">{message.role==='user'?<User size={18}/>:<Sparkle size={20} weight="fill"/>}</div>
        <div className="message-content"><span className="speaker">{message.role==='user'?'You':'Shopping Copilot'}</span><div className="bubble">{message.text}</div></div>
      </article>)}
      {!!replies.length&&<div className="reply-list">{replies.map(reply=><button className="reply" key={reply.message} disabled={disabled||!!draft.trim()} onClick={()=>shop.send(reply.message)}>{reply.label}</button>)}</div>}
      {!!shop.messages.length&&!replies.length&&<div className="next-hint"><Sparkle size={15}/><span>Make it more you. Try a different material, color, or budget.</span></div>}
      {shop.busy&&<div className="working" role="status"><span className="spinner"/>Working on your request…</div>}
      <div ref={end}/>
    </div>
    <div className="composer-wrap">
      {shop.receipt.can_undo_requirements&&<div className="history-actions"><button disabled={disabled} onClick={()=>shop.send('Undo')}><ArrowCounterClockwise size={14}/>Undo preference change</button>{shop.receipt.can_redo_requirements&&<button disabled={disabled} onClick={()=>shop.send('Redo')}><ArrowClockwise size={14}/>Redo</button>}</div>}
      <form className="composer" onSubmit={e=>{e.preventDefault();void submit()}}>
        <textarea aria-label="Message" ref={input} value={draft} onChange={e=>setDraft(e.target.value)} onCompositionStart={()=>{composing.current=true}} onCompositionEnd={()=>{composing.current=false}} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing&&!composing.current){e.preventDefault();void submit()}}} placeholder="What are you looking for?" maxLength={1000} rows={2} disabled={shop.unusable}/>
        <button className="send" type="submit" aria-label="Send message" disabled={disabled||!draft.trim()}><ArrowUp size={19} weight="bold"/></button>
      </form>
      <div className="composer-help"><span>Enter to send · Shift + Enter for a new line</span><span>{draft.length}/1000</span></div>
    </div>
  </section>;
}

export function ProductRow({product,shop}:{product:ProductCard;shop:Shopping}){
  const saved=shop.selection.selected_asins.includes(product.parent_asin);
  const disabled=shop.busy||shop.unusable;
  const full=shop.selection.selection_count>=shop.selection.max_selections;
  const evidence=product.advice?.catalog_highlights?.slice(0,2)??[];
  const pros=product.advice?.pros?.filter(p=>p.source!=='catalog_rating').slice(0,2)??[];
  return <article className="product-row">
    <span className="rank" aria-label={`Rank ${product.rank}`}>{product.rank}</span>
    <div className="product-main">
      <div className="product-heading"><div><p className="product-store">{product.store||product.category||'Catalog option'}</p><h3>{product.title}</h3></div><button className={'save '+(saved?'is-saved':'')} aria-label={`${saved?'Remove':'Save'} ${product.title}`} aria-pressed={saved} disabled={disabled||(!saved&&full)} title={!saved&&full?'Remove a saved option first':undefined} onClick={()=>shop.save(product.parent_asin,!saved)}>{saved?<Check size={16}/>:<BookmarkSimple size={16}/>}<span>{saved?'Saved':'Save'}</span></button></div>
      <div className="product-meta"><span className="price">{formatPrice(product.price)}</span><span className="rating"><Star size={14} weight="fill"/>{formatRating(product.rating)}{present(product.rating_count)&&<span className="review-count">({product.rating_count})</span>}</span></div>
      <div className="reason-grid"><div><h4>Why it may fit</h4><p className="fit-line">{matchLabel(product.match)}</p>{pros.length?<p className="reason-text">{pros[0].text}</p>:<p className="muted">Recommendation evidence is limited.</p>}</div><div className="evidence-column"><h4>Evidence</h4>{evidence.length?evidence.map((e,i)=><p className="evidence-item" key={i}><CheckCircle size={13}/><span>{e.evidence}</span></p>):<p className="muted">No additional evidence provided.</p>}</div></div>
      <div className="product-bottom"><button className="hide-button" disabled={disabled} onClick={()=>shop.send(`Hide #${product.rank}.`)}><EyeSlash size={14}/>Hide</button><button className="text-link" disabled={disabled} onClick={()=>shop.viewDetails(product.parent_asin)}>View details<ArrowRight size={16}/></button></div>
    </div>
  </article>;
}
export function ProductsPanel({shop}:{shop:Shopping}){
  return <section className="products-panel" aria-label="Products"><header className="results-heading"><div><h2>Recommended for you</h2><p>Explore the reasons behind each option.</p></div>{shop.products.length>0&&<span className="result-count">{shop.products.length} options</span>}</header>
    {shop.products.length>0?<><div className="product-list">{shop.products.map(product=><ProductRow key={product.parent_asin} product={product} shop={shop}/>)}</div><button className="text-link show-more" disabled={shop.busy||shop.unusable||shop.receipt.more_exhausted} onClick={()=>shop.send('Show me more options.')}>{shop.receipt.more_exhausted?'You’ve seen all available options':'Show more options'}<CaretDown size={16}/></button><p className="catalog-note"><Info size={14}/>Prices appear as provided; currency may be unspecified. Catalog evidence does not verify live availability.</p></>:<div className="product-empty"><ChatCircle size={44} weight="light"/><h3>{shop.messages.length?'Let’s refine your search.':'Your next find starts with a conversation.'}</h3><p>{shop.messages.length?'No matching products. Try changing a requirement.':'Tell your copilot what you need. Your recommendations will appear here.'}</p></div>}
  </section>;
}
function RequirementGroup({title,values}:{title:string;values:Requirements|undefined}){
  const items=requirementGroups(values);
  return <section className="requirement-group"><h3>{title}</h3>{items.length?<ul>{items.map(({slot,value})=><li key={`${slot}-${value}`}><span className="requirement-dot" aria-hidden="true"/><span>{humanize(value)}<small>{humanize(slot)}</small></span></li>)}</ul>:<p className="empty-label">{title==='Avoid'?'Nothing excluded yet':'None specified yet'}</p>}</section>;
}
export function DecisionRail({shop}:{shop:Shopping}){
  const s=shop.selection;const disabled=shop.busy||shop.unusable;const spots=s.max_selections-s.selection_count;
  return <aside className="decision-rail" aria-label="Saved options and preferences"><section className="saved-section"><div className="rail-heading"><h2>Saved options</h2><span>{s.selection_count} of {s.max_selections}</span></div>
    <div className="saved-list">{s.selected_products.map((p,i)=><article className="saved-row" key={p.parent_asin}><span className="saved-rank">{i+1}</span><button className="saved-title" disabled={disabled} onClick={()=>shop.viewDetails(p.parent_asin)}><strong>{p.title}</strong><span>{formatPrice(p.price)}<span className="saved-rating"><Star size={11} weight="fill"/>{present(p.rating)?p.rating:'No rating'}</span></span></button><button className="icon-button remove" aria-label={`Remove saved ${p.title}`} disabled={disabled} onClick={()=>shop.save(p.parent_asin,false)}><X size={17}/></button></article>)}</div>
    {spots>0&&<div className="saved-capacity"><Plus size={17}/><span>{s.selection_count?`${spots} more ${spots===1?'spot':'spots'} available`:'Save a few favorites to compare'}</span></div>}
    <button className="primary compare-button" disabled={disabled||s.selection_count<2} onClick={()=>shop.compare()}>Compare saved options<ArrowRight size={17}/></button>
    <p className="rail-help">{s.selection_count<2?'Save at least 2 options to compare.':'A closer look, side by side.'}</p>
    {s.finalized?<div className="confirmed"><CheckCircle size={20} weight="fill"/><div><strong>Selection confirmed</strong><span>Your decision is saved in this session.</span></div></div>:s.selection_count>0&&<button className="confirm-link" disabled={disabled} onClick={()=>shop.send('Finalize my selection.')}>Confirm selection</button>}
    {s.selection_count>0&&<div className="saved-tools"><button disabled={disabled} onClick={()=>shop.clearSaved()}>Clear saved</button>{s.can_undo_selection&&<button disabled={disabled} onClick={()=>shop.send('Undo selection.')} aria-label="Undo saved option change"><ArrowCounterClockwise size={13}/>Undo</button>}{s.can_redo_selection&&<button disabled={disabled} onClick={()=>shop.send('Redo selection.')}><ArrowClockwise size={13}/>Redo</button>}</div>}
  </section><section className="preferences-section"><div className="rail-heading"><h2>Your preferences</h2><SlidersHorizontal size={18}/></div><p className="preference-help">Shaped by your conversation.</p><RequirementGroup title="Must have" values={shop.receipt.hard}/><RequirementGroup title="Preferences" values={shop.receipt.soft}/><RequirementGroup title="Avoid" values={shop.receipt.excluded}/>{s.can_undo_rejection&&<button className="quiet-link" disabled={disabled} onClick={()=>shop.send('Undo rejection.')}><ArrowCounterClockwise size={14}/>Restore last hidden option</button>}{s.can_redo_rejection&&<button className="quiet-link" disabled={disabled} onClick={()=>shop.send('Redo rejection.')}><ArrowClockwise size={14}/>Redo hide</button>}</section></aside>;
}
