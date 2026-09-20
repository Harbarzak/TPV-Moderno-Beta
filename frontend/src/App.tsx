/**
 * Raíz del TPV: acceso (sesión) → pantalla de venta. El token no existe antes
 * de entrar, así que la decisión es binaria y barata.
 */

import LoginView from './components/LoginView';
import TpvView from './components/TpvView';
import { getToken } from './lib/api';
import { useSession } from './state/session';

export default function App() {
  const user = useSession((state) => state.user);
  if (!user && !getToken()) {
    return <LoginView />;
  }
  return <TpvView />;
}
