/**
 * Section type → component.
 *
 * The renderer knows nothing else about section types: a document naming a type that is not a key
 * here renders the labelled placeholder in `SectionRenderer`, which is what makes an older store
 * document safe to open on a newer build and the other way round.
 */

import { AnnouncementBar } from "./AnnouncementBar";
import { CardDeck } from "./CardDeck";
import { CategoryTiles } from "./CategoryTiles";
import { CollectionRow } from "./CollectionRow";
import { ContactBlock } from "./ContactBlock";
import { CredentialStrip } from "./CredentialStrip";
import { EnquiryForm } from "./EnquiryForm";
import { Faq } from "./Faq";
import { FeatureColumns } from "./FeatureColumns";
import { FooterColumns } from "./FooterColumns";
import { FooterCta } from "./FooterCta";
import { Hero } from "./Hero";
import { ImageWithText } from "./ImageWithText";
import { LogoWall } from "./LogoWall";
import { LongForm } from "./LongForm";
import { MediaGallery } from "./MediaGallery";
import { PageHeader } from "./PageHeader";
import { PriceTable } from "./PriceTable";
import { ProductCarousel } from "./ProductCarousel";
import { ProductGrid } from "./ProductGrid";
import { PromoBanner } from "./PromoBanner";
import { RichText } from "./RichText";
import { TeamGrid } from "./TeamGrid";
import { Testimonials } from "./Testimonials";
import type { SectionComponent } from "./types";
import { VideoList } from "./VideoList";

export const SECTION_COMPONENTS: Record<string, SectionComponent> = {
  hero: Hero,
  "rich-text": RichText,
  "image-with-text": ImageWithText,
  "feature-columns": FeatureColumns,
  "product-grid": ProductGrid,
  "product-carousel": ProductCarousel,
  "price-table": PriceTable,
  "category-tiles": CategoryTiles,
  "logo-wall": LogoWall,
  testimonials: Testimonials,
  faq: Faq,
  "enquiry-form": EnquiryForm,
  "contact-block": ContactBlock,
  "footer-cta": FooterCta,
  /* The retail four. Declared in `sections.retail.ts`; the two lists are kept in step by the
     library/renderer agreement test, which fails if either side grows a type the other lacks. */
  "announcement-bar": AnnouncementBar,
  "collection-row": CollectionRow,
  "promo-banner": PromoBanner,
  "footer-columns": FooterColumns,
  /* The content seven. Declared in `sections.cms.ts` — the blocks the ~120-page sitemap's company,
     support and legal pages are composed from. Same agreement test keeps the two lists in step. */
  "page-header": PageHeader,
  "long-form": LongForm,
  "media-gallery": MediaGallery,
  "team-grid": TeamGrid,
  "card-deck": CardDeck,
  "credential-strip": CredentialStrip,
  video: VideoList,
};

export function sectionComponent(type: string): SectionComponent | null {
  return SECTION_COMPONENTS[type] ?? null;
}
