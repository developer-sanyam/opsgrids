import { useRef, useState } from "react";

import { ApiError, useAsync, useCan } from "@opsgrids/client";
import {
  Button,
  Card,
  ConfirmDialog,
  ErrorState,
  InlineAlert,
  PageHeader,
  Select,
  Skeleton,
  Tag,
  TextField,
} from "@opsgrids/ui";

import { getStoreIdentity, publishStore, putStoreIdentity, unpublishStore } from "@/settings/api";
import { DomainCard } from "@/settings/DomainCard";
import {
  EMPTY_STORE_FORM,
  fromStore,
  toStorePut,
  validateStoreForm,
} from "@/settings/storeForm";
import type { StoreFormErrors, StoreFormValues } from "@/settings/storeForm";
import type { StoreOut } from "@/settings/types";

const WRITE_PERMISSION = "storefront.store.settings.write";

const CURRENCY_OPTIONS = [
  { value: "INR", label: "INR — Indian rupee" },
  { value: "USD", label: "USD — US dollar" },
  { value: "AED", label: "AED — UAE dirham" },
  { value: "GBP", label: "GBP — British pound" },
  { value: "EUR", label: "EUR — Euro" },
];

const TIMEZONE_OPTIONS = [
  { value: "Asia/Kolkata", label: "Asia/Kolkata (IST)" },
  { value: "Asia/Dubai", label: "Asia/Dubai (GST)" },
  { value: "Asia/Singapore", label: "Asia/Singapore (SGT)" },
  { value: "Europe/London", label: "Europe/London" },
  { value: "America/New_York", label: "America/New_York" },
  { value: "UTC", label: "UTC" },
];

const STATUS_TONE: Record<StoreOut["status"], "positive" | "neutral" | "warning"> = {
  published: "positive",
  draft: "neutral",
  suspended: "warning",
};

const STATUS_LABEL: Record<StoreOut["status"], string> = {
  published: "Published",
  draft: "Draft",
  suspended: "Suspended",
};

/**
 * Create the store, edit its brand, claim a domain, and publish it — the unblocker for every
 * other screen: a fresh organization has no `storefronts` row, and nothing else in this console
 * can create one.
 */
export function SettingsPage() {
  const canWrite = useCan(WRITE_PERMISSION, "ORG");
  const state = useAsync(getStoreIdentity, []);

  const [values, setValues] = useState<StoreFormValues | null>(null);
  const [errors, setErrors] = useState<StoreFormErrors>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [transitioning, setTransitioning] = useState<"published" | "draft" | null>(null);
  const [transitionError, setTransitionError] = useState<string | null>(null);
  const [confirmingUnpublish, setConfirmingUnpublish] = useState(false);
  const slugField = useRef<HTMLInputElement>(null);
  const nameField = useRef<HTMLInputElement>(null);

  // The store the form is editing: what was just saved, else what loaded, else — for a fresh
  // organization — a blank "create" form.
  const store = state.status === "success" ? state.data : null;
  const current = values ?? (store ? fromStore(store) : EMPTY_STORE_FORM);

  function set<K extends keyof StoreFormValues>(key: K, value: StoreFormValues[K]) {
    setValues({ ...current, [key]: value });
    setSaved(false);
    setErrors((prev) => {
      if (prev[key] === undefined) return prev;
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }

  async function submit() {
    if (saving) return;
    const found = validateStoreForm(current);
    setErrors(found);
    setSaveError(null);
    if (Object.keys(found).length > 0) {
      if (found.slug) slugField.current?.focus();
      else if (found.name) nameField.current?.focus();
      return;
    }
    setSaving(true);
    try {
      const saved = await putStoreIdentity(toStorePut(current));
      setValues(fromStore(saved));
      setSaved(true);
      state.reload();
    } catch (error) {
      if (error instanceof ApiError) {
        const fieldCode = error.problem?.errors?.[0];
        if (fieldCode?.field === "slug") {
          setErrors((prev) => ({ ...prev, slug: fieldCode.message }));
        } else if (fieldCode?.field !== undefined && fieldCode.field in current) {
          setErrors((prev) => ({ ...prev, [fieldCode.field]: fieldCode.message }));
        } else {
          setSaveError(error.problem?.detail ?? error.message);
        }
      } else {
        setSaveError("Couldn't reach the server. Nothing was saved — try again.");
      }
    } finally {
      setSaving(false);
    }
  }

  async function runTransition(target: "published" | "draft") {
    if (transitioning) return;
    setTransitioning(target);
    setTransitionError(null);
    try {
      await publishOrUnpublish(target);
      setConfirmingUnpublish(false);
      state.reload();
    } catch (error) {
      setTransitionError(
        error instanceof ApiError ? error.message : "Couldn't reach the server. Nothing changed.",
      );
    } finally {
      setTransitioning(null);
    }
  }

  function publishOrUnpublish(target: "published" | "draft") {
    return target === "published" ? publishStore() : unpublishStore();
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Storefront"
        title="Settings"
        description="Your store's name, address and brand — and the controls that publish it."
      />

      {state.status === "loading" ? (
        <Card className="p-6">
          <div className="flex flex-col gap-4">
            <Skeleton className="h-11 w-full" />
            <Skeleton className="h-11 w-full" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-11 w-40" />
          </div>
        </Card>
      ) : null}

      {state.status === "error" ? (
        <Card className="p-6">
          <ErrorState
            title="Settings didn't load"
            message={state.error.message}
            onRetry={state.reload}
          />
        </Card>
      ) : null}

      {state.status !== "loading" ? (
        <>
          {store === null ? (
            <InlineAlert tone="info" live="off" title="This organization has no store yet">
              Fill in the form below and save it to create your store. Nothing is visible to a
              buyer until you publish it.
            </InlineAlert>
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              <Tag tone={STATUS_TONE[store.status]}>{STATUS_LABEL[store.status]}</Tag>
              {store.status === "suspended" ? (
                <span className="text-sm text-muted">
                  Suspended by the platform. Publishing controls are unavailable here.
                </span>
              ) : canWrite ? (
                store.status === "published" ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setConfirmingUnpublish(true)}
                    disabled={transitioning !== null}
                  >
                    Unpublish
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    onClick={() => void runTransition("published")}
                    disabled={transitioning !== null}
                  >
                    {transitioning === "published" ? "Publishing…" : "Publish store"}
                  </Button>
                )
              ) : null}
            </div>
          )}

          {transitionError !== null ? (
            <InlineAlert tone="danger" title="That didn't work">
              {transitionError}
            </InlineAlert>
          ) : null}

          {!canWrite ? (
            <InlineAlert tone="warning" title="You can't change the store's settings" live="off">
              This needs storefront.store.settings.write at organization scope. An owner or a
              storefront admin can grant it.
            </InlineAlert>
          ) : null}

          <Card className="p-6">
            <div aria-live="polite" className="empty:hidden">
              {saveError !== null ? (
                <InlineAlert tone="danger" title="That wasn't saved" className="mb-4">
                  {saveError}
                </InlineAlert>
              ) : null}
              {saved ? (
                <InlineAlert tone="success" live="off" title="Saved" className="mb-4">
                  Your store's settings were saved.
                </InlineAlert>
              ) : null}
            </div>

            <form
              className="flex flex-col gap-5"
              onSubmit={(event) => {
                event.preventDefault();
                void submit();
              }}
            >
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <TextField
                  ref={slugField}
                  label="Store address"
                  value={current.slug}
                  onChange={(event) => set("slug", event.target.value.toLowerCase())}
                  placeholder="greenleaf"
                  helper={
                    store !== null
                      ? "Set once, when the store is created. It can't be changed afterwards."
                      : "Lowercase letters, digits and hyphens. This becomes part of your storefront address."
                  }
                  disabled={saving || !canWrite || store !== null}
                  {...(errors.slug !== undefined ? { error: errors.slug } : {})}
                />
                <TextField
                  ref={nameField}
                  label="Store name"
                  value={current.name}
                  onChange={(event) => set("name", event.target.value)}
                  placeholder="Greenleaf Trading Co."
                  disabled={saving || !canWrite}
                  {...(errors.name !== undefined ? { error: errors.name } : {})}
                />
              </div>

              <TextField
                label="Tagline"
                value={current.tagline}
                onChange={(event) => set("tagline", event.target.value)}
                placeholder="Reliable stock, quoted fast"
                optional
                maxLength={160}
                disabled={saving || !canWrite}
                {...(errors.tagline !== undefined ? { error: errors.tagline } : {})}
              />

              <TextField
                label="Header wordmark"
                value={current.logoText}
                onChange={(event) => set("logoText", event.target.value)}
                placeholder="GTC"
                helper="Shown in the header. Leave it empty to use the store name instead."
                optional
                maxLength={12}
                disabled={saving || !canWrite}
                {...(errors.logoText !== undefined ? { error: errors.logoText } : {})}
              />

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Select
                  label="Currency"
                  value={current.currency}
                  options={CURRENCY_OPTIONS}
                  onChange={(event) => set("currency", event.target.value)}
                  disabled={saving || !canWrite}
                  {...(errors.currency !== undefined ? { error: errors.currency } : {})}
                />
                <Select
                  label="Timezone"
                  value={current.timezone}
                  options={TIMEZONE_OPTIONS}
                  onChange={(event) => set("timezone", event.target.value)}
                  disabled={saving || !canWrite}
                  {...(errors.timezone !== undefined ? { error: errors.timezone } : {})}
                />
              </div>

              {canWrite ? (
                <div>
                  <Button type="submit" disabled={saving}>
                    {saving ? "Saving…" : store ? "Save changes" : "Create store"}
                  </Button>
                </div>
              ) : null}
            </form>
          </Card>

          {store !== null ? <DomainCard canWrite={canWrite} /> : null}
        </>
      ) : null}

      <ConfirmDialog
        open={confirmingUnpublish}
        onClose={() => {
          setConfirmingUnpublish(false);
          setTransitionError(null);
        }}
        onConfirm={() => void runTransition("draft")}
        title="Unpublish this store?"
        body="Your store goes back to draft. The public delivery check and your storefront address stop answering for buyers until you publish again."
        confirmLabel="Unpublish"
        pendingLabel="Unpublishing…"
        pending={transitioning === "draft"}
        {...(transitionError !== null ? { error: transitionError } : {})}
      />
    </div>
  );
}
