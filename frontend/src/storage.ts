// localStorage, що не падає: у приватному режимі чи з вимкненими даними сайту
// він просто нічого не пам'ятає, а сторінка працює як завжди.

/** Значення за ключем (JSON) або запасне, якщо його немає чи прочитати не можна. */
export function load<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw == null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

/** Зберегти значення як JSON; null чи undefined видаляють ключ. */
export function save(key: string, value: unknown) {
  try {
    if (value == null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* не запам'ятаємо — і гаразд */
  }
}
