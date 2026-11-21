import { FlaskConical } from "lucide-react";

export default function Banner() {
  return (
    <div className="banner" role="note">
      <FlaskConical size={14} aria-hidden="true" />
      Test mode: simulated payments, never enter a real card.
    </div>
  );
}
