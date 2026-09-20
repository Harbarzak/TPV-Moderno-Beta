/**
 * Teclado numérico táctil (PIN e importes): botones grandes de 64 px.
 * No interpreta nada: emite dígitos, coma decimal y borrado; el sentido de
 * la entrada lo decide la pantalla que lo usa.
 */

interface KeypadProps {
  onDigit: (digit: string) => void;
  onDot?: () => void;
  onBackspace: () => void;
  /** Oculta la coma decimal (PIN, cantidades enteras). */
  hideDot?: boolean;
}

const DIGITS = ['1', '2', '3', '4', '5', '6', '7', '8', '9'] as const;

export default function Keypad({ onDigit, onDot, onBackspace, hideDot = false }: KeypadProps) {
  return (
    <div className="grid grid-cols-3 gap-2">
      {DIGITS.map((digit) => (
        <button key={digit} type="button" className="btn-secondary h-16 text-xl font-semibold" onClick={() => onDigit(digit)}>
          {digit}
        </button>
      ))}
      {hideDot ? (
        <span aria-hidden className="h-16" />
      ) : (
        <button type="button" className="btn-secondary h-16 text-xl font-semibold" onClick={onDot}>
          ,
        </button>
      )}
      <button type="button" className="btn-secondary h-16 text-xl font-semibold" onClick={() => onDigit('0')}>
        0
      </button>
      <button type="button" className="btn-secondary h-16 text-xl font-semibold" onClick={onBackspace}>
        ⌫
      </button>
    </div>
  );
}
