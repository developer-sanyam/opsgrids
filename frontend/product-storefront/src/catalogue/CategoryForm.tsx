import { useRef, useState } from "react";

import {
  ASSET_ACCEPTED_TYPES,
  ASSET_MAX_UPLOAD_BYTES,
  ASSET_PUBLIC_HELPER,
  problemMessage,
  uploadAsset,
  useMutation,
} from "@opsgrids/client";
import type { ApiError } from "@opsgrids/client";
import { FileUpload, FormDialog, Switch, TextArea, TextField } from "@opsgrids/ui";
import type { StoredFile } from "@opsgrids/ui";

import { UPLOAD_NEEDS_SERVICE, USE_FIXTURES } from "@/api/client";
import { resolveImageSrc } from "@/theme/render/assetSrc";
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

    void save.mutate({
      key: key.trim(),
      label: label.trim(),
      blurb: blurb.trim() === "" ? null : blurb.trim(),
      image_url: imageUrl.trim() === "" ? null : imageUrl.trim(),
      sort_order: category?.sort_order ?? 0,
      is_active: isActive,
    });
  }

  // The category's existing image, carried into the field as its one starting file — `category`
  // never changes while this dialog is open.
  const currentImagePreview = category?.image_url ? resolveImageSrc(category.image_url) : null;
  const initialImageFiles: StoredFile<string>[] = category?.image_url
    ? [
        {
          name: "Current image",
          value: category.image_url,
          ...(currentImagePreview !== null ? { previewUrl: currentImagePreview } : {}),
        },
      ]
    : [];

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
      <FileUpload<string>
        label="Category image"
        accept={[...ASSET_ACCEPTED_TYPES]}
        maxSize={ASSET_MAX_UPLOAD_BYTES}
        maxFiles={1}
        optional
        helper={ASSET_PUBLIC_HELPER}
        initialFiles={initialImageFiles}
        disabled={save.pending}
        error={save.fieldErrors.image_url}
        onUpload={async (file, controls) => {
          if (USE_FIXTURES) throw new Error(UPLOAD_NEEDS_SERVICE);
          const asset = await uploadAsset(file, controls);
          return asset.public_path;
        }}
        onChange={(files) => setImageUrl(files[0] ?? "")}
      />
      <Switch
        label="Active"
        checked={isActive}
        onChange={setIsActive}
        description="Inactive categories are hidden from buyers but keep their products."
      />
    </FormDialog>
  );
}
