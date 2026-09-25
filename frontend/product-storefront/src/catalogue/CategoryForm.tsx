import { useRef, useState } from "react";

import { useMutation } from "@opsgrids/client";
import type { ApiError } from "@opsgrids/client";
import { FormDialog, Switch, TextArea, TextField } from "@opsgrids/ui";
import { problemMessage } from "@opsgrids/client";

import { isHttpsImageUrl } from "./imageUrl";
import { createCategory, updateCategory } from "./api";
import type { CategoryInput, CategoryOut } from "./types";

const KEY_PATTERN = /^[a-z0-9][a-z0-9-]*$/;

export function CategoryForm({
  category,
  onClose,
  onSaved,
}: {
  /** `null` creates. */
  category: CategoryOut | null;
  onClose: () => void;
  onSaved: (category: CategoryOut) => void;
}) {
  const [key, setKey] = useState(category?.key ?? "");
  const [label, setLabel] = useState(category?.label ?? "");
  const [blurb, setBlurb] = useState(category?.blurb ?? "");
  const [imageUrl, setImageUrl] = useState(category?.image_url ?? "");
  const [isActive, setIsActive] = useState(category?.is_active ?? true);
  const [keyError, setKeyError] = useState<string | undefined>(undefined);
  const [labelError, setLabelError] = useState<string | undefined>(undefined);
  const [imageError, setImageError] = useState<string | undefined>(undefined);
  const keyRef = useRef<HTMLInputElement>(null);
  const labelRef = useRef<HTMLInputElement>(null);

  // Compared against the same seed the fields above initialised from — this form unmounts on
  // close (FormDialog's resetting-is-structural note), so `category` never changes under it.
  const dirty =
    key !== (category?.key ?? "") ||
    label !== (category?.label ?? "") ||
    blurb !== (category?.blurb ?? "") ||
    imageUrl !== (category?.image_url ?? "") ||
    isActive !== (category?.is_active ?? true);

  const save = useMutation(
    (input: CategoryInput) =>
      category ? updateCategory(category.id, input) : createCategory(input),
    { onSuccess: (saved) => { onClose(); onSaved(saved); } },
  );

  function submit() {
    setKeyError(undefined);
    setLabelError(undefined);
    setImageError(undefined);

    if (!category && !KEY_PATTERN.test(key.trim())) {
      setKeyError("Lowercase letters, digits and hyphens, starting with a letter or digit.");
      keyRef.current?.focus();
      return;
    }
    if (label.trim().length === 0) {
      setLabelError("Give the category a label.");
      labelRef.current?.focus();
      return;
    }
    const trimmedImage = imageUrl.trim();
    if (trimmedImage !== "" && !isHttpsImageUrl(trimmedImage)) {
      setImageError("Enter a full https:// image address.");
      return;
    }

    void save.mutate({
      key: key.trim(),
      label: label.trim(),
      blurb: blurb.trim() === "" ? null : blurb.trim(),
      image_url: trimmedImage === "" ? null : trimmedImage,
      sort_order: category?.sort_order ?? 0,
      is_active: isActive,
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={category ? `Edit ${category.label}` : "New category"}
      submitLabel={category ? "Save changes" : "Create category"}
      pendingLabel={category ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={category ? labelRef : keyRef}
      dirty={dirty}
      {...(save.error && Object.keys(save.fieldErrors).length === 0
        ? { error: problemMessage(save.error), reference: (save.error as ApiError).problem?.request_id }
        : {})}
    >
      <TextField
        ref={keyRef}
        label="Key"
        value={key}
        onChange={(event) => setKey(event.target.value.toLowerCase())}
        helper={category ? "Set once, when the category is created." : "Lowercase, e.g. fasteners."}
        disabled={category !== null}
        required
        error={keyError ?? save.fieldErrors.key}
      />
      <TextField
        ref={labelRef}
        label="Label"
        value={label}
        onChange={(event) => setLabel(event.target.value)}
        required
        error={labelError ?? save.fieldErrors.label}
      />
      <TextArea
        label="Blurb"
        value={blurb}
        onChange={(event) => setBlurb(event.target.value)}
        optional
        rows={2}
        maxLength={200}
        error={save.fieldErrors.blurb}
      />
      <TextField
        label="Image address"
        value={imageUrl}
        onChange={(event) => setImageUrl(event.target.value)}
        placeholder="https://"
        optional
        helper="A full https:// address, with a preview below. Uploads aren't available on this box yet."
        error={imageError ?? save.fieldErrors.image_url}
      />
      {imageUrl.trim() !== "" && isHttpsImageUrl(imageUrl.trim()) ? (
        <div className="h-24 w-full overflow-hidden rounded-tile border border-line bg-ground">
          <img src={imageUrl.trim()} alt="" className="h-full w-full object-cover" />
        </div>
      ) : null}
      <Switch
        label="Active"
        checked={isActive}
        onChange={setIsActive}
        description="Inactive categories are hidden from buyers but keep their products."
      />
    </FormDialog>
  );
}
