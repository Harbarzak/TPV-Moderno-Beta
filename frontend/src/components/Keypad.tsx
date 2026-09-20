/**
 * Teclado numérico propio (design-system.md §4): en táctil NUNCA el teclado
 * del SO. Teclas de 56 px (el cobro pide lo grande), «00» y coma incluidas.
 * La edición del búfer vive en lib/keypad.ts; este componente solo pinta.
 */

import { Delete } from 'lucide-react';
import type { KeypadKey } from '../lib/keypad';

interface KeypadProps {
  onKey: (key: KeypadKey) => void;
  /** Tecla grande de confirmar (✓). Sin onSubmit no se pinta. */
  onSubmit?: () => void;
  submitLabel?: string;
  submitDisabled?: boolean;
  className?: string;
}

const KEY =
  'flex h-14 items-center justify-center rounded-xl bg-surface-sunken text-xl font-semibold ' +
  'text-ink transition hover:bg-surface-hover active:scale-[0.98]';

export default function Keypad({
  onKey,
  onSubmit,
  submitLabel,
  submitDisabled = false,
  className = '',
}: KeypadProps) {
  return (
    <div className={`grid grid-cols-4 gap-2 ${className}`}>
      {(['7', '8', '9'] as const).map((key) => (
        <button key={key} type="button" onClick={() => onKey(key)} className={KEY}>
          {key}
        </button>
      ))}
      <button
        type="button"
        onClick={() => onKey('back')}
        aria-label="Borrar última cifra"
        className={KEY}
      >
        <Delete className="size-6" aria-hidden />
      </button>
      {(['4', '5', '6'] as const).map((key) => (
        <button key={key} type="button" onClick={() => onKey(key)} className={KEY}>
          {key}
        </button>
      ))}
      <button
        type="button"
        onClick={() => onKey('clear')}
        aria-label="Borrar todo"
        className={`${KEY} text-ink-muted`}
      >
        C
      </button>
      {(['1', '2', '3'] as const).map((key) => (
        <button key={key} type="button" onClick={() => onKey(key)} className={KEY}>
          {key}
        </button>
      ))}
      <button type="button" onClick={() => onKey('00')} className={KEY}>
        00
      </button>
      <button type="button" onClick={() => onKey('0')} className={`${KEY} col-span-2`}>
        0
      </button>
      <button type="button" onClick={() => onKey(',')} className={`${KEY} col-span-2`}>
        ,
      </button>
      {onSubmit && (
        <button
          type="button"
          onClick={onSubmit}
          disabled={submitDisabled}
          className="col-span-4 flex h-14 items-center justify-center rounded-xl bg-accent text-xl font-bold text-accent-ink transition hover:bg-accent-hover active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40"
        >
          {submitLabel ?? 'Aceptar'}
        </button>
      )}
    </div>
  );
}
