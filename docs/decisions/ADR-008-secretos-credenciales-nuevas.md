# ADR-008 — Secretos nuevos y rotados; prohibición absoluta de heredar credenciales del legado

- **Estado:** Aceptada
- **Fecha:** 2026-09-11
- **Fase:** 00 · Arquitectura

## Contexto

El sistema legado configura credenciales en `VisTPV.ini`. Migrar esos valores literalmente
reproduciría superficies de compromiso antiguas (usuarios/contraseñas de BD conocidas, históricas
y posiblemente compartidas).

## Decisión

- **Prohibido** incorporar al proyecto —documentación, código, seeds o backups— ninguna
  credencial, cadena de conexión o dato sensible del `VisTPV.ini`.
- Todo secreto se genera **nuevo y rotado**, y vive solo en variables de entorno / secret local
  cifrado; `.env` fuera del repositorio con plantilla `.env.example` sin valores reales.
- Cada servicio con el principio de mínimo privilegio (usuario de BD exclusivo del servidor,
  tokens de dispositivo por terminal, JWT de usuario, tokens de agente).
- Rueda de secretos documentada en la fase 23 (Seguridad): rotación de BD, JWT signing keys,
  CA local y tokens de terminales.

## Consecuencias

- Ninguna fuga del proyecto compromete el entorno legado, y viceversa.
- El instalador (fase 22) genera secretos en el momento de instalar; no existen valores por
  defecto conocidos.
