import {useState} from 'react';
import {Circle,ChatCircle,Rows,BookmarkSimple,X,WarningCircle,CheckCircle} from '@phosphor-icons/react';
import {useShopping,DEMO} from './useShopping';
import {ChatPanel,ProductsPanel,DecisionRail} from './components';
import {DetailDrawer,ComparisonDialog} from './Overlays';

export function App(){
  const shop=useShopping();const [tab,setTab]=useState('products');
  return <><header className="topbar"><a className="brand" href="#main" aria-label="Shopping Copilot"><Circle weight="fill" className="brand-mark" size={31}/><span>Shopping Copilot</span></a><span className="mode-label" title={shop.disclosure}>{DEMO?'Demo catalog':'Shopping assistant'}</span><button className="new-conversation secondary" disabled={shop.busy} onClick={()=>{void shop.start(false);setTab('chat')}}><ChatCircle size={17}/>New conversation</button></header>
    {shop.error&&<div className="error-banner" role="alert"><WarningCircle size={20}/><span>{shop.error}</span>{!shop.busy&&<button onClick={()=>shop.start(false)}>Start new conversation</button>}</div>}
    <main id="main" className={`workspace mobile-${tab}`} aria-busy={shop.busy}><ChatPanel shop={shop}/><ProductsPanel shop={shop}/><DecisionRail shop={shop}/></main>
    <nav className="mobile-nav" aria-label="Main views">{[{id:'chat',label:'Chat',Icon:ChatCircle},{id:'products',label:'Products',Icon:Rows},{id:'compare',label:'Compare',Icon:BookmarkSimple}].map(({id,label,Icon})=><button key={id} aria-current={tab===id?'page':undefined} onClick={()=>setTab(id)}><Icon size={22} weight={tab===id?'fill':'regular'}/>{label}{id==='compare'&&shop.selection.selection_count>0&&<span>{shop.selection.selection_count}</span>}</button>)}</nav>
    {shop.notice&&!shop.error&&<div className="toast" role="status"><CheckCircle size={19}/><span>{shop.notice}</span><button className="icon-button" aria-label="Dismiss notification" onClick={shop.dismissNotice}><X size={16}/></button></div>}
    {shop.detail&&<DetailDrawer shop={shop}/>}{shop.comparison&&shop.compareOpen&&<ComparisonDialog shop={shop}/>}
  </>;
}
