import {useEffect,useRef,useState} from 'react';
import {api,ApiError,errorCopy} from './api';
import type {ChatMessage,ChatResponse,Handoff,Health,ProductCard,ProductDetail,Receipt,SelectionState,SessionResponse} from './contracts';

const emptySelection=():SelectionState=>({selected_asins:[],selected_products:[],selection_count:0,max_selections:3,status:'draft',finalized:false});
export const DEMO=import.meta.env.VITE_DATA_MODE==='demo';
const sample='I need a blue dress. I prefer cotton.';
export function useShopping(){
  const [session,setSession]=useState('');
  const [messages,setMessages]=useState<ChatMessage[]>([]);
  const [products,setProducts]=useState<ProductCard[]>([]);
  const [receipt,setReceipt]=useState<Receipt>({});
  const [selection,setSelection]=useState<SelectionState>(emptySelection);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [unusable,setUnusable]=useState(false);
  const [notice,setNotice]=useState('');
  const [detail,setDetail]=useState<ProductDetail|null>(null);
  const [comparison,setComparison]=useState<Handoff|null>(null);
  const [compareOpen,setCompareOpen]=useState(false);
  const [disclosure,setDisclosure]=useState('');
  const lock=useRef(false);
  const initialized=useRef(false);
  const clear=()=>{setMessages([]);setProducts([]);setReceipt({});setSelection(emptySelection());setDetail(null);setComparison(null);setCompareOpen(false);setNotice('')};
  async function run(task:()=>Promise<void>){
    if(lock.current)return false;
    lock.current=true;setBusy(true);setError('');
    try{await task();return true}catch(e){
      setError(errorCopy(e));
      if(e instanceof ApiError && ['session_expired','session_not_found','turn_limit'].includes(e.code)){clear();setSession('');setUnusable(true)}
      if(e instanceof ApiError && ['timeout','network_error','invalid_response'].includes(e.code))setUnusable(true);
      return false;
    }finally{lock.current=false;setBusy(false)}
  }
  function apply(response:ChatResponse){
    setProducts([...response.products].sort((a,b)=>a.rank-b.rank));
    setReceipt(response.receipt??{});
    setSelection(response.selection_state);
    setComparison(response.handoff??null);
    if(response.handoff)setCompareOpen(true);
    else setCompareOpen(false);
    if(response.receipt?.selection_finalization_invalidated)setNotice('Your preferences changed. Review your options and confirm again.');
  }
  async function start(seed=false){
    return run(async()=>{
      clear();setSession('');setUnusable(false);
      const health=await api.get<Health>('/api/health');setDisclosure(health.data_disclosure??'');
      const created=await api.post<SessionResponse>('/api/session',{});setSession(created.session_id);
      if(seed&&DEMO&&!health.search_backend){
        const response=await api.post<ChatResponse>('/api/chat',{session_id:created.session_id,message:sample});
        apply(response);
        setMessages([{id:'sample-user',role:'user',text:sample},{id:'sample-assistant',role:'assistant',text:response.assistant.message}]);
        for(const product of response.products.slice(0,2)){
          const selected=await api.post<SelectionState>('/api/select',{session_id:created.session_id,parent_asin:product.parent_asin,selected:true});setSelection(selected);
        }
      }
    });
  }
  useEffect(()=>{if(!initialized.current){initialized.current=true;void start(DEMO)}},[]);
  async function send(text:string){
    const message=text.trim();
    if(!message||!session||unusable)return false;
    if(message.length>1000){setError('Keep your message to 1,000 characters.');return false}
    return run(async()=>{
      const wasFinal=selection.finalized;
      const response=await api.post<ChatResponse>('/api/chat',{session_id:session,message});
      apply(response);
      setMessages(previous=>[...previous,{id:crypto.randomUUID(),role:'user',text:message},{id:crypto.randomUUID(),role:'assistant',text:response.assistant.message}]);
      if(wasFinal&&!response.selection_state.finalized)setNotice('Your selection changed. Review your options and confirm again.');
    });
  }
  async function save(id:string,selected:boolean){
    if(!session||unusable)return false;
    return run(async()=>{
      const response=await api.post<SelectionState>('/api/select',{session_id:session,parent_asin:id,selected});
      setSelection(response);setComparison(null);setCompareOpen(false);
      setNotice(selection.finalized&&!response.finalized?'Your saved options changed. Confirm your selection again.':selected?'Option saved.':'Option removed.');
    });
  }
  async function clearSaved(){
    if(!session||unusable)return false;
    return run(async()=>{const response=await api.post<SelectionState>('/api/select',{session_id:session,clear:true});setSelection(response);setComparison(null);setCompareOpen(false);setNotice('Saved options cleared.')});
  }
  async function viewDetails(id:string){
    if(!session||unusable)return false;
    return run(async()=>setDetail(await api.post<ProductDetail>('/api/product',{session_id:session,parent_asin:id})));
  }
  async function compare(){
    if(selection.selection_count<2||!session||unusable)return false;
    return run(async()=>{const data=await api.post<Handoff>('/api/handoff',{session_id:session});setComparison(data);setCompareOpen(true)});
  }
  return {session,messages,products,receipt,selection,busy,error,unusable,notice,detail,comparison,compareOpen,disclosure,
    start,send,save,clearSaved,viewDetails,compare,closeDetail:()=>setDetail(null),closeComparison:()=>setCompareOpen(false),dismissNotice:()=>setNotice('')};
}
export type Shopping=ReturnType<typeof useShopping>;
