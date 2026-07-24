/**
 * Groups the flat model roster into optgroup-ready sections for the per-phase
 * model picker: cloud models under "Cloud", and each local server under
 * "provider · host" (e.g. "oMLX · studio.riechers.co:8000").
 *
 * A model is "local" when it carries a `host` (set by the backend roster for
 * discovered endpoints); cloud models have none.
 */
export interface RosterModel {
  id: string
  name: string
  provider: string
  host?: string | null
  backend?: string | null
}

export interface ModelGroup {
  label: string
  models: RosterModel[]
}

export function groupAvailableModels(models: RosterModel[]): ModelGroup[] {
  const byName = (a: RosterModel, b: RosterModel) => a.name.localeCompare(b.name)

  const cloud = models.filter((m) => !m.host)
  const localByHost = new Map<string, RosterModel[]>()
  for (const m of models) {
    if (!m.host) continue
    const label = `${m.provider} · ${m.host}`
    const arr = localByHost.get(label) ?? []
    arr.push(m)
    localByHost.set(label, arr)
  }

  const groups: ModelGroup[] = []
  if (cloud.length) groups.push({ label: 'Cloud', models: [...cloud].sort(byName) })
  for (const label of [...localByHost.keys()].sort()) {
    groups.push({ label, models: [...localByHost.get(label)!].sort(byName) })
  }
  return groups
}
