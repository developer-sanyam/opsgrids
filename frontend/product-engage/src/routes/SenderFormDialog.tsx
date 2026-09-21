import { useState } from "react";

import { addSender } from "@/api/client";
import type { DomainOut, SenderIn, SenderOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { Button, Dialog, InlineAlert, Select, TextField } from "@/ui";
import type { SelectOption } from "@/ui";

/**
 * Add a named identity mail may go out as.
 *
 * **Only verified domains are offered.** `services.create_sender` refuses a sender on a domain
 * that has not proven its DNS, so listing a pending one would be offering a choice that ends in a
 * refusal — and the refusal would arrive after the customer had typed five fields.
 *
 * Whether the address belongs to the chosen domain is not checked here. That is a fact about rows
 * rather than about the string, the writer decides it, and its refusal names both halves
 * (`sales@a.com is not an address on b.com`) far better than a regular expression could.
 */

interface Values {
  domainId: string;
  name: string;
  address: string;
  replyTo: string;
  department: string;
}

type Errors = Partial<Record<keyof Values, string>>;

const EMPTY: Values = { domainId: "", name: "", address: "", replyTo: "", department: "" };

function validate(values: Values): Errors {
  const errors: Errors = {};
  if (values.domainId === "") errors.domainId = "Which domain does this sender send from?";
  if (values.name.trim() === "") {
    errors.name = "The name recipients see in their inbox, like “Vaayu Support”.";
  }
  if (values.address.trim() === "") errors.address = "The address mail goes out from.";
  return errors;
}

function toInput(values: Values): SenderIn {
  return {
    domain_id: values.domainId,
    name: values.name.trim(),
    address: values.address.trim(),
    // Omitted rather than sent empty where the customer left them alone: the service defaults
    // both to "", and a reply-to of "" means replies go to the sending address.
    ...(values.replyTo.trim() === "" ? {} : { reply_to: values.replyTo.trim() }),
    ...(values.department.trim() === "" ? {} : { department: values.department.trim() }),
  };
}

export function SenderFormDialog({
  open,
  domains,
  onClose,
  onSaved,
}: {
  open: boolean;
  /** Verified domains only — the caller filters, because it is the one holding the list. */
  domains: DomainOut[];
  onClose: () => void;
  onSaved: (sender: SenderOut, message: string) => void;
}) {
  const [values, setValues] = useState<Values>(EMPTY);
  const [errors, setErrors] = useState<Errors>({});
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  /**
   * The typed fields survive a close and the messages do not. Reopening to a cleared form after
   * a stray Escape costs the customer five fields; reopening to last time's refusal tells them
   * something that is no longer true about what they are looking at.
   */
  function close() {
    setErrors({});
    setFailure(null);
    onClose();
  }

  function set<K extends keyof Values>(key: K, value: Values[K]) {
    setValues((current) => ({ ...current, [key]: value }));
    setErrors((current) => {
      if (current[key] === undefined) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  async function submit() {
    if (submitting) return;
    const found = validate(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      setFailure(null);
      return;
    }

    setSubmitting(true);
    setFailure(null);
    try {
      const sender = await addSender(toInput(values));
      setValues(EMPTY);
      onSaved(sender, `${sender.address} added. Send it a test message to make it usable.`);
    } catch (caught) {
      // Everything typed stays exactly where it is. A form that empties itself on a refusal
      // costs the customer the work twice, and this one refuses for reasons worth correcting.
      setFailure(failureMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  const domainOptions: SelectOption[] = domains.map((domain) => ({
    value: domain.id,
    label: domain.domain,
  }));
  const chosen = domains.find((domain) => domain.id === values.domainId) ?? null;

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Add a sender"
      description="The name and address a message arrives from. It cannot send anything until a test message has reached your provider."
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={submitting}>
            {submitting ? "Adding…" : "Add sender"}
          </Button>
        </>
      }
    >
      <div aria-live="polite" className="empty:hidden">
        {failure !== null ? (
          <InlineAlert tone="danger" title="That sender wasn't added" className="mb-4">
            {failure}
          </InlineAlert>
        ) : null}
        {failure === null && Object.keys(errors).length > 0 ? (
          <InlineAlert tone="warning" title="Check the fields marked below" className="mb-4">
            Nothing has been sent yet.
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
        <Select
          label="Domain"
          value={values.domainId}
          onChange={(event) => set("domainId", event.target.value)}
          options={domainOptions}
          placeholder="Choose a verified domain"
          helper="Only verified domains can carry a sender."
          disabled={submitting}
          {...(errors.domainId !== undefined ? { error: errors.domainId } : {})}
        />

        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="Name"
            value={values.name}
            onChange={(event) => set("name", event.target.value)}
            placeholder="Vaayu Logistics Support"
            helper="What recipients see in their inbox."
            disabled={submitting}
            {...(errors.name !== undefined ? { error: errors.name } : {})}
          />
          <TextField
            label="Address"
            type="email"
            value={values.address}
            onChange={(event) => set("address", event.target.value)}
            placeholder={chosen === null ? "support@your-domain.com" : `support@${chosen.domain}`}
            autoComplete="off"
            spellCheck={false}
            helper={
              chosen === null
                ? "An address on the domain you choose above."
                : `An address on ${chosen.domain}.`
            }
            disabled={submitting}
            {...(errors.address !== undefined ? { error: errors.address } : {})}
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="Reply-to"
            type="email"
            value={values.replyTo}
            onChange={(event) => set("replyTo", event.target.value)}
            optional
            autoComplete="off"
            spellCheck={false}
            helper="Where replies land. Left empty, they go back to the sending address."
            disabled={submitting}
          />
          <TextField
            label="Department"
            value={values.department}
            onChange={(event) => set("department", event.target.value)}
            optional
            placeholder="Support"
            helper="Your own label for grouping senders. Recipients never see it."
            disabled={submitting}
          />
        </div>
      </form>
    </Dialog>
  );
}
