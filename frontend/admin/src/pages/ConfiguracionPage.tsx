/**
 * Configuración del negocio (/admin/business-settings + /admin/logo):
 * datos fiscales para los documentos y logo en PNG/JPEG (base64, sin
 * prefijo data: en el PUT).
 */

import { useState } from 'react';
import { apiFetch } from '../lib/api';
import { businessSettingsSchema, type BusinessSettings } from '../lib/domain';
import {
  Banner,
  Field,
  Loading,
  PageHeader,
  useAction,
  useAsync,
} from '../components/ui';

export default function ConfiguracionPage() {
  const { data, error, loading, reload } = useAsync(
    () =>
      apiFetch<unknown>('/api/v1/admin/business-settings')
        .then(businessSettingsSchema.parse),
    [],
  );

  if (loading) {
    return (
      <>
        <PageHeader title="Configuración" hint="Datos del negocio para facturas y tiquets." />
        <div className="card">
          <Loading />
        </div>
      </>
    );
  }
  if (error || !data) {
    return (
      <>
        <PageHeader title="Configuración" hint="Datos del negocio para facturas y tiquets." />
        <Banner onClose={reload}>{error ?? 'Sin datos'}</Banner>
      </>
    );
  }

  return (
    <>
      <PageHeader title="Configuración" hint="Datos del negocio para facturas y tiquets." />
      <div className="grid gap-4 lg:grid-cols-2">
        <BusinessForm settings={data} onSaved={reload} />
        <LogoCard settings={data} onChanged={reload} />
      </div>
    </>
  );
}

function BusinessForm({ settings, onSaved }: { settings: BusinessSettings; onSaved: () => void }) {
  const action = useAction();
  const [name, setName] = useState(settings.business.name ?? '');
  const [taxId, setTaxId] = useState(settings.business.tax_id ?? '');
  const [address, setAddress] = useState(settings.business.address ?? '');
  const [phone, setPhone] = useState(settings.business.phone ?? '');

  const save = () => {
    void action.run(async () => {
      await apiFetch('/api/v1/admin/business-settings', {
        method: 'PATCH',
        body: JSON.stringify({ name, tax_id: taxId, address, phone }),
      });
      onSaved();
    });
  };

  return (
    <div className="card">
      <h2 className="mb-3 text-sm font-bold uppercase tracking-wider text-slate-400">Datos fiscales</h2>
      {action.error && <Banner onClose={action.clear}>{action.error}</Banner>}
      <Field label="Razón social">
        <input className="input-base" value={name} onChange={(e) => setName(e.target.value)} maxLength={160} />
      </Field>
      <div className="mt-3">
        <Field label="NIF / CIF">
          <input className="input-base" value={taxId} onChange={(e) => setTaxId(e.target.value)} maxLength={32} />
        </Field>
      </div>
      <div className="mt-3">
        <Field label="Dirección">
          <input className="input-base" value={address} onChange={(e) => setAddress(e.target.value)} maxLength={240} />
        </Field>
      </div>
      <div className="mt-3">
        <Field label="Teléfono">
          <input className="input-base" value={phone} onChange={(e) => setPhone(e.target.value)} maxLength={32} />
        </Field>
      </div>
      <div className="mt-4 flex justify-end">
        <button type="button" className="btn-primary" onClick={save} disabled={action.busy}>
          {action.busy ? 'Guardando…' : 'Guardar datos'}
        </button>
      </div>
    </div>
  );
}

function LogoCard({ settings, onChanged }: { settings: BusinessSettings; onChanged: () => void }) {
  const action = useAction();
  const [localError, setLocalError] = useState<string | null>(null);

  const logo = settings.logo as { mime?: unknown; data?: unknown } | null;
  const logoSrc =
    logo && typeof logo.mime === 'string' && typeof logo.data === 'string'
      ? `data:${logo.mime};base64,${logo.data}`
      : null;

  const upload = (file: File | undefined) => {
    setLocalError(null);
    if (!file) return;
    if (file.type !== 'image/png' && file.type !== 'image/jpeg') {
      setLocalError('El logo debe ser PNG o JPEG.');
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => setLocalError('No se pudo leer el fichero.');
    reader.onload = () => {
      const result = String(reader.result ?? '');
      const base64 = result.slice(result.indexOf(',') + 1);
      void action.run(async () => {
        await apiFetch('/api/v1/admin/logo', {
          method: 'PUT',
          body: JSON.stringify({ mime: file.type, data: base64 }),
        });
        onChanged();
      });
    };
    reader.readAsDataURL(file);
  };

  const remove = () => {
    if (!window.confirm('¿Quitar el logo actual?')) return;
    void action.run(async () => {
      await apiFetch('/api/v1/admin/logo', { method: 'DELETE' });
      onChanged();
    });
  };

  return (
    <div className="card">
      <h2 className="mb-3 text-sm font-bold uppercase tracking-wider text-slate-400">Logo</h2>
      {(action.error ?? localError) && (
        <Banner onClose={() => {
          action.clear();
          setLocalError(null);
        }}>
          {action.error ?? localError}
        </Banner>
      )}
      {logoSrc ? (
        <img src={logoSrc} alt="Logo del negocio" className="mb-3 max-h-28 rounded-lg bg-white ring-1 ring-slate-200" />
      ) : (
        <p className="mb-3 text-sm text-slate-400">Sin logo. Se usará el nombre en los documentos.</p>
      )}
      <label className="block">
        <span className="label-base">Nuevo logo (PNG o JPEG)</span>
        <input
          type="file"
          accept="image/png,image/jpeg"
          className="input-base"
          onChange={(e) => upload(e.target.files?.[0])}
          disabled={action.busy}
        />
      </label>
      {logoSrc && (
        <div className="mt-4 flex justify-end">
          <button type="button" className="btn-danger" onClick={remove} disabled={action.busy}>
            Quitar logo
          </button>
        </div>
      )}
    </div>
  );
}
