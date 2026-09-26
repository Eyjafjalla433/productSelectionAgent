export type CatalogValue = string | number | null | undefined;
export type Requirements = Record<string, string | string[] | number | null>;
export interface Signal { tier: string; slot: string; value: string; status: string; evidence?: string }
export interface Match { hard_supported: number; hard_total: number; soft_supported?: number; soft_total?: number; hard_coverage?: number; signals?: Signal[]; disclaimer?: string }
export interface AdviceItem {text:string;source?:string;evidence?:string}
export interface Advice {pros?:AdviceItem[];cons?:AdviceItem[];catalog_highlights?:{source?:string;evidence:string}[];disclaimer?:string}
export interface ProductCard {parent_asin:string;rank:number;title:string;store?:string;price:CatalogValue;rating:CatalogValue;rating_count?:CatalogValue;category?:string;features?:string[];match?:Match;advice?:Advice;shopper_notes?:{feature?:string;detail?:string;fit?:string;caution?:string}}
export interface ProductDetail {parent_asin:string;title:string;store?:string;price:CatalogValue;average_rating:CatalogValue;rating_number?:CatalogValue;categories?:string[];product_description?:string[];product_bullet_points?:string[];details?:Record<string,unknown>;requirement_match?:Match;advice?:Advice;source_note?:string;last_observed_rank?:number}
export type SavedProduct=Pick<ProductCard,'parent_asin'|'title'|'price'|'rating'|'match'|'advice'>;
export interface SelectionState {selected_asins:string[];selected_products:SavedProduct[];selection_count:number;max_selections:number;can_undo_selection?:boolean;can_redo_selection?:boolean;can_undo_rejection?:boolean;can_redo_rejection?:boolean;rejected_asins?:string[];status:string;finalized:boolean;finalized_at_utc?:string;finalized_turn?:number}
export interface Question {options?:string[];option_labels?:Record<string,string>;correction?:unknown;target_slot?:string}
export interface Receipt {hard?:Requirements;soft?:Requirements;excluded?:Requirements;question?:Question|null;suggested_replies?:string[];detail_question?:unknown;comparison_reference_question?:unknown;preference_comparison?:{question?:unknown};can_undo_requirements?:boolean;can_redo_requirements?:boolean;more_exhausted?:boolean;requirements_changed?:boolean;selection_finalization_invalidated?:boolean;state_evidence?:Record<string,Record<string,{evidence?:string}>>}
export interface ComparisonCell {value:unknown;evidence?:unknown;source_field?:string|null;source_type?:string|null}
export interface MatrixRow {dimension:string;values:Record<string,ComparisonCell>}
export interface Handoff {selected_products:ProductDetail[];saved_asins?:string[];comparison_scope?:string;requirements?:{hard?:Requirements;soft?:Requirements;excluded?:Requirements};comparison_summary?:{rows:{parent_asin:string;title?:string;price:CatalogValue;rating:CatalogValue;hard_coverage?:number}[]};comparison_assist?:{status?:string;objective_comparison?:{comparison_matrix?:MatrixRow[]}};decision?:{finalized:boolean;selected_asins?:string[]};status?:string}
export interface ChatResponse {assistant:{message:string};products:ProductCard[];receipt:Receipt;selection_state:SelectionState;shopping_guide?:{intro?:string;data_note?:string};handoff?:Handoff;turn:number;remaining_turns:number|null}
export interface SessionResponse {session_id:string;scenarios?:{title:string;label:string}[];data_disclosure?:string}
export interface Health {ok:boolean;data_disclosure?:string;search_backend?:string;model_provider?:string}
export interface ChatMessage {id:string;role:'user'|'assistant';text:string}
