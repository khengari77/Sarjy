<script lang="ts">
  interface Props {
    name: string;
    result?: unknown;
    arabic: boolean;
  }

  let { name, result, arabic }: Props = $props();

  const FRIENDLY: Record<string, { en: string; ar: string }> = {
    get_weather: { en: 'Checked weather', ar: 'تحقّق من الطقس' },
    weather: { en: 'Checked weather', ar: 'تحقّق من الطقس' },
    search_memory: { en: 'Searched memory', ar: 'بحث في الذاكرة' },
    propose_context_exclusion: { en: 'Prepared a context change', ar: 'جهّز تغييرًا في السياق' }
  };

  function location(value: unknown): string | null {
    if (!value || typeof value !== 'object') return null;
    const record = value as Record<string, unknown>;
    const city = record.city ?? record.location ?? record.name;
    return typeof city === 'string' && city.trim() ? city.trim().slice(0, 60) : null;
  }

  const city = $derived(location(result));
  const base = $derived(FRIENDLY[name]?.[arabic ? 'ar' : 'en'] ?? (arabic ? 'أداة' : `Used ${name}`));
  const label = $derived(city ? `${base} · ${city}` : base);
</script>

<span class="chip">{label}</span>

<style>
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 999px;
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    color: var(--text-muted);
    font-size: 12px;
    white-space: nowrap;
  }
</style>
