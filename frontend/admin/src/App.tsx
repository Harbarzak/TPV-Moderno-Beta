/**
 * Raíz del panel de administración: restauración de sesión al arrancar,
 * acceso si no la hay y despacho de la sección activa según los PERMISOS
 * que emitió el backend (visibilidad local; la autoridad es el servidor).
 * Sin router: una sección por estado, como el resto de apps del proyecto.
 */

import { useEffect, useState } from 'react';
import { useSession } from './state/session';
import { sectionsFor, type SectionId } from './lib/permissions';
import Login from './components/Login';
import Shell from './components/Shell';
import ProductosPage from './pages/ProductosPage';
import CategoriasPage from './pages/CategoriasPage';
import DepartamentosPage from './pages/DepartamentosPage';
import PanelesPage from './pages/PanelesPage';
import UsuariosPage from './pages/UsuariosPage';
import PermisosPage from './pages/PermisosPage';
import CamarerosPage from './pages/CamarerosPage';
import FormasPagoPage from './pages/FormasPagoPage';
import TerminalesPage from './pages/TerminalesPage';
import DispositivosPage from './pages/DispositivosPage';
import ImpresorasPage from './pages/ImpresorasPage';
import ConfiguracionPage from './pages/ConfiguracionPage';
import BackupsPage from './pages/BackupsPage';
import AuditoriaPage from './pages/AuditoriaPage';

export default function App() {
  const { me, ready, restore, logout } = useSession();
  const [section, setSection] = useState<SectionId | null>(null);

  useEffect(() => {
    void restore();
  }, [restore]);

  if (!ready) {
    return <div className="grid h-dvh place-items-center text-sm text-slate-400">Administración…</div>;
  }

  if (!me) {
    return <Login />;
  }

  const sections = sectionsFor(me.permissions);
  const active: SectionId | null =
    section !== null && sections.some((item) => item.id === section)
      ? section
      : (sections[0]?.id ?? null);

  return (
    <Shell
      me={me}
      sections={sections}
      active={active}
      onSelect={(id) => setSection(id as SectionId)}
      onLogout={() => {
        setSection(null);
        void logout();
      }}
    >
      {active === 'productos' && <ProductosPage />}
      {active === 'categorias' && <CategoriasPage />}
      {active === 'departamentos' && <DepartamentosPage />}
      {active === 'paneles' && <PanelesPage />}
      {active === 'usuarios' && <UsuariosPage />}
      {active === 'permisos' && <PermisosPage />}
      {active === 'camareros' && <CamarerosPage />}
      {active === 'formas-pago' && <FormasPagoPage />}
      {active === 'terminales' && <TerminalesPage />}
      {active === 'dispositivos' && <DispositivosPage />}
      {active === 'impresoras' && <ImpresorasPage />}
      {active === 'configuracion' && <ConfiguracionPage />}
      {active === 'backups' && <BackupsPage />}
      {active === 'auditoria' && <AuditoriaPage />}
    </Shell>
  );
}
