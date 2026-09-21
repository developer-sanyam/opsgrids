# CRUD.md — the contract for create, edit and delete

Everything below already exists in `@opsgrids/ui` and `@opsgrids/client`. **Build none of it
again.** An app may not fork a component into itself (ADR-0001); if a slice needs a variant, it
lands in `opsgrids-ui` as a prop. Read `frontend/CLAUDE.md` first — this is the CRUD-shaped
subset of those rules, not a replacement for them.

## What you import

```ts
import {
  Button, Can, ConfirmDialog, DataTable, EmptyState, ErrorState, FormDialog,
  Pagination, RowActions, SearchInput, Select, Skeleton, TextField, Toaster, useToast,
} from "@opsgrids/ui";
import { ApiError, apiRequest, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
```

`<Toaster>` is mounted once, at the app root above the router. `<Announcer>` stays per screen for
announcements that are not toasts — **never say the same sentence through both.**

## Permissions

`PermissionProvider` (inside `AuthProvider`) fetches `GET /api/v1/members/me/grants` after sign-in
and again when the organization, membership or token `pv` changes. Gate every write control:

```tsx
<Can permission="operations.procurement.supplier.write" scope="ORG">
  <Button onClick={() => setCreating(true)}>New supplier</Button>
</Can>
```

Pass the **same scope the endpoint's guard passes**. `useCan(permission, scope)` is the hook form,
for a `RowAction.hidden`. While grants load, `Can` renders nothing; if they fail it renders
nothing and `usePermissions().status` is `"error"` — the app shell says so, screens do not.
`<RequirePermission>` is the screen-level version, with an explanatory `fallback`.

This hides UI. The server refuses regardless, so still render the 403 you may get.

## Errors

Failures are RFC 7807. Field detail arrives as `errors: [{field, code, message}]`
(`libs/platform-core/src/platform_core/errors.py:150`), with the `body.`/`query.` prefix already
stripped — key on `name`, or `parts.0.sku` for a nested one. `useMutation` turns it into
`fieldErrors`; `problemMessage(error)` is the sentence for everything else. Never retry a 4xx —
`useMutation` never retries anything.

## The list

```tsx
function Widgets() {
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" }>({ key: "name", dir: "asc" });
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [editing, setEditing] = useState<Widget | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Widget | null>(null);

  const widgets = useAsync(
    () => listWidgets({ q: query, sort: sort.key, dir: sort.dir, page, size }),
    [query, sort.key, sort.dir, page, size],
  );

  const remove = useMutation((widget: Widget) => deleteWidget(widget.id), {
    onSuccess: (_result, widget) => {
      setDeleting(null);
      toast.success(`${widget.name} deleted.`);
      widgets.reload();
    },
  });

  if (widgets.status === "loading") return <Skeleton className="h-64 w-full" />;
  if (widgets.status === "error") {
    return (
      <ErrorState
        title="Couldn't load widgets"
        message={problemMessage(widgets.error)}
        action={<Button onClick={widgets.reload}>Try again</Button>}
      />
    );
  }

  const { items, total, pages } = widgets.data;
  return (
    <>
      <DataTable
        rows={items}
        rowKey={(w) => w.id}
        sort={sort}
        onSort={(key) => setSort((s) => ({ key, dir: s.key === key && s.dir === "asc" ? "desc" : "asc" }))}
        toolbar={
          <div className="flex items-center justify-between gap-4">
            <SearchInput label="Search widgets" value={query}
              onChange={(e) => { setQuery(e.target.value); setPage(1); }} />
            <Can permission="ops.widget.write" scope="ORG">
              <Button onClick={() => setCreating(true)}>New widget</Button>
            </Can>
          </div>
        }
        emptyState={
          <EmptyState
            title={query ? "No widgets match that search" : "No widgets yet"}
            description={query ? "Try a shorter search term." : "Create the first one to get started."}
            action={query ? <Button variant="outline" onClick={() => setQuery("")}>Clear search</Button> : null}
          />
        }
        columns={[
          { key: "name", header: "Name", sortKey: "name", render: (w) => w.name },
          { key: "sku", header: "SKU", sortKey: "sku", cellClassName: "tnum", render: (w) => w.sku },
          {
            key: "actions", header: "", align: "right",
            render: (w) => (
              <RowActions
                label={w.name}
                actions={[
                  { label: "Edit", onSelect: () => setEditing(w), hidden: !canWrite },
                  { label: "Delete", tone: "danger", onSelect: () => setDeleting(w), hidden: !canWrite,
                    disabled: w.in_use, disabledReason: "In use by an open order." },
                ]}
              />
            ),
          },
        ]}
      />
      <Pagination page={page} pages={pages} total={total} size={size} noun="widgets"
        onPageChange={setPage} onSizeChange={(n) => { setSize(n); setPage(1); }} />
      {/* dialogs below */}
    </>
  );
}
```

`const canWrite = useCan("ops.widget.write", "ORG")` at the top of the component.
`LoadMore` instead of `Pagination` only for an append-style queue with no sorting.

## Create and edit

One `WidgetForm`, mounted only while the dialog is open — **that unmount is the reset.** Never
hoist field state into the screen and clear it by hand.

```tsx
function WidgetForm({ widget, onClose, onSaved }: { widget: Widget | null; onClose: () => void; onSaved: (w: Widget) => void }) {
  const [name, setName] = useState(widget?.name ?? "");
  const [sku, setSku] = useState(widget?.sku ?? "");
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const nameRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    (input: WidgetIn) => (widget ? updateWidget(widget.id, input) : createWidget(input)),
    { onSuccess: (saved) => { onClose(); onSaved(saved); } },
  );

  function submit() {
    setNameError(undefined);
    if (name.trim().length < 2) {           // our validation, not the browser's
      setNameError("Give it a name of at least 2 characters.");
      nameRef.current?.focus();
      return;
    }
    void save.mutate({ name: name.trim(), sku: sku.trim() });
  }

  return (
    <FormDialog
      open onClose={onClose} onSubmit={submit}
      title={widget ? `Edit ${widget.name}` : "New widget"}
      submitLabel={widget ? "Save changes" : "Create widget"}
      pendingLabel={widget ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={nameRef}
      {...(save.error && Object.keys(save.fieldErrors).length === 0
        ? { error: problemMessage(save.error), reference: (save.error as ApiError).problem?.request_id }
        : {})}
    >
      <TextField ref={nameRef} label="Name" value={name} onChange={(e) => setName(e.target.value)}
        error={nameError ?? save.fieldErrors.name} />
      <TextField label="SKU" value={sku} onChange={(e) => setSku(e.target.value)}
        helper="The supplier's code." error={save.fieldErrors.sku} />
    </FormDialog>
  );
}
```

Rendered from the screen, and the toast lives with the screen so it survives the dialog closing:

```tsx
{creating ? <WidgetForm widget={null} onClose={() => setCreating(false)}
  onSaved={(w) => { toast.success(`${w.name} created.`); widgets.reload(); }} /> : null}
{editing ? <WidgetForm widget={editing} onClose={() => setEditing(null)}
  onSaved={(w) => { toast.success(`${w.name} saved.`); widgets.reload(); }} /> : null}
```

A field error goes on its field. `FormDialog`'s `error` is only for a failure that belongs to no
field — a 409, a 403, a 500.

## Delete

```tsx
<ConfirmDialog
  open={deleting !== null}
  onClose={() => { setDeleting(null); remove.reset(); }}
  onConfirm={() => deleting && void remove.mutate(deleting)}
  title="Delete this widget?"
  body={`${deleting?.name} and its revisions are removed. This can't be undone.`}
  confirmLabel="Delete"
  pendingLabel="Deleting…"
  pending={remove.pending}
  {...(remove.error ? { error: problemMessage(remove.error) } : {})}
/>
```

Focus opens on Cancel, confirm fires once per attempt, and the dialog will not close mid-flight.

## Rules

1. Every write goes through `useMutation`. No bare `try/catch` around `apiRequest` in a screen.
2. Every list has all four states: loading, empty, error, full. A screen with only the full state
   is half a screen.
3. Reload after a write (`useAsync(...).reload`). Never patch the local array — the server's
   filter, sort and `total` decide what belongs on the page.
4. Field errors on fields, and move focus to the first one.
5. Toast on success, from the screen, not from inside the dialog.
6. Spacing from `4 · 8 · 12 · 16 · 24 · 32 · 48 · 64`. Colour from the semantic names only. No raw
   hex, ever.
7. Need a component, a prop or a token that is not here? It comes to `opsgrids-ui`. Do not add it
   to your app.
