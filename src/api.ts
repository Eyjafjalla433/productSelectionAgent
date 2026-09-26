export class ApiError extends Error {
  constructor(public code:string,message:string){super(message);this.name='ApiError'}
}
export class ApiClient {
  private tail:Promise<unknown>=Promise.resolve();
  constructor(private transport:typeof fetch=(input,init)=>globalThis.fetch(input,init)){}
  post<T>(path:string,body:unknown):Promise<T>{
    const task=this.tail.then(()=>this.request<T>(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}));
    this.tail=task.catch(()=>undefined);
    return task;
  }
  get<T>(path:string){return this.request<T>(path,{method:'GET'})}
  private async request<T>(path:string,options:RequestInit):Promise<T>{
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(),45000);
    try {
      const response=await this.transport(path,{...options,signal:controller.signal});
      let data;
      try { data=await response.json(); } catch {throw new ApiError('invalid_response','The service returned an unreadable response. Please start a new conversation if the request changed your selection.');}
      if(!response.ok)throw new ApiError(data.error_code??'request_failed',data.error??'Request failed.');
      return data as T;
    }catch(error){
      if(error instanceof ApiError)throw error;
      throw new ApiError(controller.signal.aborted?'timeout':'network_error','We could not confirm this request. It may have completed. Your message is kept; please start a new conversation before trying again.');
    }finally{clearTimeout(timer)}
  }
}
export const api=new ApiClient();
export function errorCopy(error:unknown):string {
  if(!(error instanceof ApiError))return 'Something went wrong. Please try again.';
  const labels:Record<string,string>={
    session_not_found:'Your session has ended. Start a new conversation.',session_expired:'Your session has ended. Start a new conversation.',turn_limit:'Your session has ended. Start a new conversation.',
    product_not_shown:'This product is not available in this conversation.',catalog_product_not_found:'Details are unavailable for this product.',selection_limit:'Your saved options are full. Remove an option first.',empty_message:'Enter a message to continue.',message_too_long:'Keep your message to 1,000 characters.',selection_reason_too_long:'Keep your note to 300 characters.',body_too_large:'This request is too large. Please shorten your message.',internal_error:'The request failed. Your message is kept. Please start a new conversation if the request may have changed your selection.'
  };
  return labels[error.code]??(['timeout','network_error','invalid_response'].includes(error.code)?error.message:'We could not complete that request. Please check your input and try again.');
}
