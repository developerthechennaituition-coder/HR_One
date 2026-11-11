import React from "react";

export default function Pagination({ page, totalItems, pageSize = 10, onPageChange }) {
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const current = Math.min(page, totalPages);
  if (totalItems <= pageSize) return null;
  return (
    <nav className="d-flex flex-wrap align-items-center justify-content-between gap-2 px-3 py-3" aria-label="Records pagination">
      <small className="text-muted">Page {current} of {totalPages} · {totalItems} records</small>
      <div className="btn-group btn-group-sm" role="group">
        <button type="button" className="btn btn-outline-secondary" disabled={current <= 1} onClick={() => onPageChange(current - 1)}>Previous</button>
        <button type="button" className="btn btn-outline-secondary" disabled={current >= totalPages} onClick={() => onPageChange(current + 1)}>Next</button>
      </div>
    </nav>
  );
}
