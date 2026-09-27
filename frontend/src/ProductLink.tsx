import {ArrowUpRight} from '@phosphor-icons/react';
import type {ProductLink as ProductLinkData} from './contracts';
import {amazonProductUrl} from './model';

export function ProductLink({product}: {product: ProductLinkData}) {
  const url = amazonProductUrl(product.product_url);
  if (!url) return null;
  return <div className="product-external-link">
    <a href={url} target="_blank" rel="noopener noreferrer"
       aria-label="View on Amazon (opens in a new tab)">
      View on Amazon <ArrowUpRight size={15} aria-hidden="true"/>
    </a>
    {product.url_verified !== true && <span className="product-link-note">Link not verified</span>}
  </div>;
}
