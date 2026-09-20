import { z } from 'zod';

/** Contratos del panel de administración (§3: dinero string, sin float). */

export const uuid = z.string().uuid();

/** Identidad emitida por GET /auth/me — los permisos son siempre los que el
 *  servidor acaba de calcular, nunca los que el cliente suponga. */
export const meSchema = z.object({
  id: uuid,
  username: z.string(),
  full_name: z.string(),
  role: z.string(),
  permissions: z.array(z.string()),
  scope: z.string(),
});
export type Me = z.infer<typeof meSchema>;

/** Página estándar del contrato API: { items, total, limit, offset }. */
export function paged<T>(itemSchema: z.ZodType<T>) {
  return z.object({
    items: z.array(itemSchema),
    total: z.number().int(),
    limit: z.number().int(),
    offset: z.number().int(),
  });
}
export type Page<T> = { items: T[]; total: number; limit: number; offset: number };

/** Mensaje simple de operación (patrón {message} del backend). */
export const messageSchema = z.object({ message: z.string() });
