import { useRef, useState } from "react";

import { setSupplierProfile } from "@/api/client";
import type { SupplierProfileUpsert, SupplierSettings } from "@/api/types";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, TextField, useFormDirty } from "@/ui";

const PAN_PATTERN = /^[A-Z]{5}[0-9]{4}[A-Z]$/;
const IFSC_PATTERN = /^[A-Z]{4}0[A-Z0-9]{6}$/;
const ACCOUNT_PATTERN = /^[0-9]{9,18}$/;
const UPI_PATTERN = /^[A-Za-z0-9._-]{2,256}@[A-Za-z][A-Za-z0-9.-]{1,63}$/;

interface Props {
  settings: SupplierSettings;
  onClose: () => void;
  onSaved: (settings: SupplierSettings) => void;
}

/**
 * PAN and the bank account customers pay into — `PUT /finance/settings/supplier-profile`.
 *
 * The account number is write-only: the field starts empty with the current number's mask as its
 * hint, and leaving it empty keeps the number on file. Only what changed is sent, so a save here
 * can never blank a field somebody else set.
 */
export function SupplierProfileFormDialog({ settings, onClose, onSaved }: Props) {
  const current = settings.profile;
  const gstinPan = settings.registration?.gstin.slice(2, 12) ?? "";
  const [pan, setPan] = useState(current.pan ?? gstinPan);
  const [accountName, setAccountName] = useState(current.bank.account_name);
  const [accountNumber, setAccountNumber] = useState("");
  const [ifsc, setIfsc] = useState(current.bank.ifsc);
  const [bankName, setBankName] = useState(current.bank.bank_name);
  const [branch, setBranch] = useState(current.bank.branch);
  const [upi, setUpi] = useState(current.bank.upi_id);

  const fields = { pan, accountName, accountNumber, ifsc, bankName, branch, upi };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);
  const [errors, setErrors] = useState<Partial<Record<keyof typeof fields, string>>>({});
  const panRef = useRef<HTMLInputElement>(null);

  const save = useMutation((input: SupplierProfileUpsert) => setSupplierProfile(input), {
    onSuccess: (saved) => onSaved(saved),
  });

  function submit() {
    const next: typeof errors = {};
    const cleanPan = pan.trim().toUpperCase();
    if (cleanPan !== "" && !PAN_PATTERN.test(cleanPan)) next.pan = "A PAN is five letters, four numerals and a letter.";
    else if (cleanPan !== "" && gstinPan !== "" && cleanPan !== gstinPan) next.pan = `Your GSTIN carries the PAN ${gstinPan}.`;
    const cleanAccount = accountNumber.replace(/[\s-]/g, "");
    if (cleanAccount !== "" && !ACCOUNT_PATTERN.test(cleanAccount)) next.accountNumber = "An account number is 9 to 18 numerals.";
    const cleanIfsc = ifsc.trim().toUpperCase();
    if (cleanIfsc !== "" && !IFSC_PATTERN.test(cleanIfsc)) next.ifsc = "An IFSC is four letters, a zero and six letters or numerals.";
    if (upi.trim() !== "" && !UPI_PATTERN.test(upi.trim())) next.upi = "A UPI ID looks like name@bank.";
    setErrors(next);
    if (Object.keys(next).length > 0) return;

    const input: SupplierProfileUpsert = {};
    if (cleanPan !== (current.pan ?? "")) input.pan = cleanPan;
    if (accountName.trim() !== current.bank.account_name) input.bank_account_name = accountName.trim();
    if (cleanAccount !== "") input.bank_account_number = cleanAccount;
    if (cleanIfsc !== current.bank.ifsc) input.bank_ifsc = cleanIfsc;
    if (bankName.trim() !== current.bank.bank_name) input.bank_name = bankName.trim();
    if (branch.trim() !== current.bank.branch) input.bank_branch = branch.trim();
    if (upi.trim() !== current.bank.upi_id) input.upi_id = upi.trim();
    void save.mutate(input);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="PAN and bank details"
      description="Printed on every tax invoice so your customer knows where to pay. Optional, but an invoice without them sends the customer back to ask."
      submitLabel="Save"
      pendingLabel="Saving…"
      pending={save.pending}
      initialFocusRef={panRef}
      dirty={dirty}
      {...(save.error
        ? {
            error: problemMessage(save.error),
            ...(save.error instanceof ApiError && save.error.problem?.request_id ? { reference: save.error.problem.request_id } : {}),
          }
        : {})}
    >
      <TextField
        ref={panRef}
        label="PAN"
        value={pan}
        onChange={(e) => setPan(e.target.value.toUpperCase())}
        maxLength={10}
        optional
        helper={gstinPan ? `Must match the PAN in your GSTIN (${gstinPan}).` : undefined}
        error={errors.pan}
      />
      <TextField label="Account name" value={accountName} onChange={(e) => setAccountName(e.target.value)} maxLength={200} optional />
      <TextField
        label="Account number"
        value={accountNumber}
        inputMode="numeric"
        autoComplete="off"
        onChange={(e) => setAccountNumber(e.target.value)}
        maxLength={24}
        optional
        helper={
          current.bank.account_number
            ? `On file: ${current.bank.account_number}. Leave empty to keep it.`
            : "Stored encrypted and shown masked everywhere except the printed invoice."
        }
        error={errors.accountNumber}
      />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField label="IFSC" value={ifsc} onChange={(e) => setIfsc(e.target.value.toUpperCase())} maxLength={11} optional error={errors.ifsc} />
        <TextField label="UPI ID" value={upi} onChange={(e) => setUpi(e.target.value)} maxLength={100} optional error={errors.upi} />
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField label="Bank" value={bankName} onChange={(e) => setBankName(e.target.value)} maxLength={120} optional />
        <TextField label="Branch" value={branch} onChange={(e) => setBranch(e.target.value)} maxLength={120} optional />
      </div>
    </FormDialog>
  );
}
