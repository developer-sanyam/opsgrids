import { today } from "@/lib/now";
import { TextArea, TextField } from "@/ui";

interface DateAndNoteFieldsProps {
  occurredOn: string;
  onOccurredOn: (value: string) => void;
  note: string;
  onNote: (value: string) => void;
  dateError?: string | undefined;
  noteError?: string | undefined;
}

/** The two fields every stock operation ends with: the business date (never in the future — the
 *  server refuses that too) and an optional note. */
export function DateAndNoteFields({
  occurredOn,
  onOccurredOn,
  note,
  onNote,
  dateError,
  noteError,
}: DateAndNoteFieldsProps) {
  return (
    <>
      <TextField
        label="Date"
        type="date"
        value={occurredOn}
        max={today()}
        onChange={(e) => onOccurredOn(e.target.value)}
        required
        error={dateError}
      />
      <TextArea
        label="Note"
        value={note}
        onChange={(e) => onNote(e.target.value)}
        optional
        maxLength={500}
        counterFrom={400}
        error={noteError}
      />
    </>
  );
}
