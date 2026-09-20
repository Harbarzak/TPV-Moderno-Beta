/**
 * Shell del KDS: restauración de sesión al arrancar y dos pantallas — acceso
 * o tablero. Sin navegación: la cocina solo mira y toca comandas.
 */

import { useEffect } from 'react';
import { useSession } from './state/session';
import LoginScreen from './screens/LoginScreen';
import BoardScreen from './screens/BoardScreen';

export default function App() {
  const { me, ready, restore } = useSession();

  useEffect(() => {
    void restore();
  }, [restore]);

  if (!ready) {
    return <div className="grid h-dvh place-items-center text-slate-400">Cargando…</div>;
  }
  return me === null ? <LoginScreen /> : <BoardScreen />;
}
