import { useEffect, useId, useMemo, useRef, useState } from "react";

/*
 * Searchable multi-select used by every Create Notification target type
 * (roles, departments, employees, reporting managers). Selected items are
 * shown as removable chips; the options come from the backend lookups.
 *
 * options: [{ id, label, meta? }]   value: string[] of option ids
 */
function NotificationTargetSelect({
  id,
  label,
  options = [],
  value = [],
  onChange,
  placeholder = "Select",
  searchPlaceholder = "Search",
  emptyText = "No options available",
  noun = "item",
  disabled = false,
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef(null);
  const listId = useId();

  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => {
      if (rootRef.current && !rootRef.current.contains(event.target)) setOpen(false);
    };
    const onKey = (event) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const byId = useMemo(() => new Map(options.map((option) => [String(option.id), option])), [options]);
  const selected = useMemo(() => new Set(value.map(String)), [value]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return options;
    return options.filter((option) => `${option.label} ${option.meta || ""}`.toLowerCase().includes(needle));
  }, [options, query]);

  const toggle = (optionId) => {
    const key = String(optionId);
    onChange(selected.has(key) ? value.filter((item) => String(item) !== key) : [...value, key]);
  };
  const allFilteredSelected = filtered.length > 0 && filtered.every((option) => selected.has(String(option.id)));
  const toggleAllFiltered = () => {
    const ids = filtered.map((option) => String(option.id));
    onChange(allFilteredSelected
      ? value.filter((item) => !ids.includes(String(item)))
      : [...new Set([...value.map(String), ...ids])]);
  };

  return (
    <div className="nts" ref={rootRef}>
      <label className="form-label" htmlFor={id}>{label}</label>
      <button
        id={id}
        type="button"
        className={`nts-control form-select${open ? " is-open" : ""}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
      >
        <span className={value.length ? "nts-control-text" : "nts-control-text nts-placeholder"}>
          {value.length ? `${value.length} ${noun}${value.length === 1 ? "" : "s"} selected` : placeholder}
        </span>
      </button>

      {open && (
        <div className="nts-panel" id={listId} role="listbox" aria-multiselectable="true" aria-label={label}>
          <div className="nts-search">
            <i className="bi bi-search" aria-hidden="true" />
            <input
              type="search"
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={searchPlaceholder}
              aria-label={searchPlaceholder}
            />
          </div>
          {filtered.length > 0 && (
            <button type="button" className="nts-all" onClick={toggleAllFiltered}>
              {allFilteredSelected ? "Clear" : "Select all"}{query.trim() ? " matching" : ""} ({filtered.length})
            </button>
          )}
          <ul className="nts-options">
            {filtered.length === 0 ? (
              <li className="nts-empty">{options.length === 0 ? emptyText : "No matches found"}</li>
            ) : filtered.map((option) => {
              const checked = selected.has(String(option.id));
              return (
                <li key={option.id}>
                  <label className={`nts-option${checked ? " is-selected" : ""}`}>
                    <input type="checkbox" checked={checked} onChange={() => toggle(option.id)} />
                    <span className="nts-option-label">{option.label}</span>
                    {option.meta && <span className="nts-option-meta">{option.meta}</span>}
                  </label>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {value.length > 0 && (
        <div className="nts-chips" aria-label={`Selected ${noun}s`}>
          {value.map((item) => {
            const option = byId.get(String(item));
            const text = option ? option.label : String(item);
            return (
              <span className="nts-chip" key={item}>
                <span className="nts-chip-text" title={text}>{text}</span>
                <button type="button" className="nts-chip-remove" aria-label={`Remove ${text}`} onClick={() => toggle(item)} disabled={disabled}>
                  <i className="bi bi-x" aria-hidden="true" />
                </button>
              </span>
            );
          })}
          <button type="button" className="nts-clear" onClick={() => onChange([])} disabled={disabled}>Clear all</button>
        </div>
      )}
    </div>
  );
}

export default NotificationTargetSelect;
