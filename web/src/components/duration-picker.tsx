"use client";

import { KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { Flag, TimerReset } from "lucide-react";

const ROW_HEIGHT = 44;
const UNKNOWN_DURATION_LIMIT = 23 * 60 * 60 + 59 * 60 + 59;

function numberRange(maximum: number) {
  return Array.from({ length: maximum + 1 }, (_, index) => index);
}

function padded(value: number) {
  return String(value).padStart(2, "0");
}

function readableDuration(totalSeconds: number) {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours) return `${hours} hour${hours === 1 ? "" : "s"}, ${minutes} minute${minutes === 1 ? "" : "s"}, ${seconds} second${seconds === 1 ? "" : "s"}`;
  return `${minutes} minute${minutes === 1 ? "" : "s"}, ${seconds} second${seconds === 1 ? "" : "s"}`;
}

function compactDuration(totalSeconds: number) {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours
    ? `${hours}:${padded(minutes)}:${padded(seconds)}`
    : `${minutes}:${padded(seconds)}`;
}

function scrollBehavior(): ScrollBehavior {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}

function WheelColumn({
  label,
  maximum,
  onChange,
  value,
}: {
  label: string;
  maximum: number;
  onChange: (value: number) => void;
  value: number;
}) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const values = useMemo(() => numberRange(maximum), [maximum]);

  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    const target = Math.min(value, maximum) * ROW_HEIGHT;
    if (Math.abs(scroller.scrollTop - target) > ROW_HEIGHT / 2) {
      scroller.scrollTo({ top: target, behavior: "auto" });
    }
  }, [maximum, value]);

  function handleScroll() {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    const nextValue = Math.max(0, Math.min(maximum, Math.round(scroller.scrollTop / ROW_HEIGHT)));
    if (nextValue !== value) onChange(nextValue);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    let nextValue = value;
    if (event.key === "ArrowUp") nextValue = Math.min(maximum, value + 1);
    else if (event.key === "ArrowDown") nextValue = Math.max(0, value - 1);
    else if (event.key === "PageUp") nextValue = Math.min(maximum, value + 10);
    else if (event.key === "PageDown") nextValue = Math.max(0, value - 10);
    else if (event.key === "Home") nextValue = 0;
    else if (event.key === "End") nextValue = maximum;
    else return;
    event.preventDefault();
    onChange(nextValue);
  }

  return (
    <div className="duration-wheel-column">
      <span className="duration-wheel-label">{label}</span>
      <div className="duration-wheel-selection" aria-hidden="true" />
      <div
        aria-label={label}
        aria-valuemax={maximum}
        aria-valuemin={0}
        aria-valuenow={value}
        aria-valuetext={`${value} ${label.toLowerCase()}`}
        className="duration-wheel"
        onKeyDown={handleKeyDown}
        onScroll={handleScroll}
        ref={scrollerRef}
        role="spinbutton"
        tabIndex={0}
      >
        {values.map((option) => (
          <div
            aria-hidden="true"
            className={`duration-wheel-option ${option === value ? "is-selected" : ""}`}
            key={option}
            onClick={() => {
              onChange(option);
              scrollerRef.current?.scrollTo({ top: option * ROW_HEIGHT, behavior: scrollBehavior() });
            }}
          >
            {padded(option)}
          </div>
        ))}
      </div>
    </div>
  );
}

export function DurationPicker({
  allowTrackEnd,
  initialSeconds,
  label,
  maxSeconds,
  onCancel,
  onConfirm,
  onUseTrackEnd,
}: {
  allowTrackEnd: boolean;
  initialSeconds: number;
  label: string;
  maxSeconds: number;
  onCancel: () => void;
  onConfirm: (seconds: number) => void;
  onUseTrackEnd: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const limit = maxSeconds > 0 ? Math.floor(maxSeconds) : UNKNOWN_DURATION_LIMIT;
  const [selectedSeconds, setSelectedSeconds] = useState(() => Math.max(0, Math.min(Math.round(initialSeconds), limit)));
  const clampedSeconds = Math.min(selectedSeconds, limit);
  const hours = Math.floor(clampedSeconds / 3600);
  const minutes = Math.floor((clampedSeconds % 3600) / 60);
  const seconds = clampedSeconds % 60;
  const maximumHours = Math.floor(limit / 3600);
  const maximumMinutes = hours === maximumHours ? Math.floor((limit % 3600) / 60) : 59;
  const maximumSeconds = hours === maximumHours && minutes === maximumMinutes ? limit % 60 : 59;

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialogRef.current?.focus({ preventScroll: true });

    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not(:disabled), [role="spinbutton"], [href], [tabindex]:not([tabindex="-1"])',
      ) ?? []);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [onCancel]);

  function updateSelection(nextHours: number, nextMinutes: number, nextSeconds: number) {
    setSelectedSeconds(Math.min(limit, nextHours * 3600 + nextMinutes * 60 + nextSeconds));
  }

  return (
    <div className="duration-picker-backdrop">
      <div
        aria-describedby="duration-picker-description"
        aria-labelledby="duration-picker-title"
        aria-modal="true"
        className="duration-picker-sheet"
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <div className="duration-picker-toolbar">
          <button className="duration-picker-toolbar-button" onClick={onCancel} type="button">Cancel</button>
          <button className="duration-picker-toolbar-button is-done" onClick={() => onConfirm(clampedSeconds)} type="button">Done</button>
        </div>

        <div className="duration-picker-heading">
          <span><TimerReset size={19} /></span>
          <div>
            <h2 id="duration-picker-title">{label}</h2>
            <p id="duration-picker-description">Scroll each wheel, then tap Done.{maxSeconds > 0 ? ` Maximum ${compactDuration(limit)}.` : ""}</p>
          </div>
        </div>

        <div className="duration-wheels" aria-label={`Selected duration: ${readableDuration(clampedSeconds)}`}>
          <WheelColumn label="Hours" maximum={maximumHours} onChange={(value) => updateSelection(value, minutes, seconds)} value={hours} />
          <WheelColumn label="Minutes" maximum={maximumMinutes} onChange={(value) => updateSelection(hours, value, seconds)} value={minutes} />
          <WheelColumn label="Seconds" maximum={maximumSeconds} onChange={(value) => updateSelection(hours, minutes, value)} value={seconds} />
        </div>

        <p className="duration-picker-readout" aria-live="polite">{compactDuration(clampedSeconds)}</p>

        {allowTrackEnd && (
          <button className="duration-track-end" disabled={maxSeconds <= 0} onClick={onUseTrackEnd} type="button">
            <Flag size={16} />Use exact track end{maxSeconds > 0 ? ` · ${compactDuration(Math.round(maxSeconds))}` : ""}
          </button>
        )}
      </div>
    </div>
  );
}
