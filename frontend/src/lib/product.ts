/**
 * Adaptación de productos del catálogo (snapshot o botón de panel) a la línea
 * del ticket: la forma que el carrito necesita para vender sin más consultas.
 */

import type { CartProduct } from '../state/cart';

interface CatalogLike {
  id: string;
  name: string;
  price: string;
  tax_code: string;
  tax_rate: string;
  weighable: boolean;
}

export function toCartProduct(product: CatalogLike): CartProduct {
  return {
    id: product.id,
    name: product.name,
    price: product.price,
    taxCode: product.tax_code,
    taxRate: product.tax_rate,
    weighable: product.weighable,
  };
}
