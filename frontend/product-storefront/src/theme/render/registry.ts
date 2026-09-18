/**
 * Section type → component.
 *
 * The renderer knows nothing else about section types: a document naming a type that is not a key
 * here renders the labelled placeholder in `SectionRenderer`, which is what makes an older store
 * document safe to open on a newer build and the other way round.
 */

import { CategoryTiles } from "./CategoryTiles";
import { ContactBlock } from "./ContactBlock";
import { EnquiryForm } from "./EnquiryForm";
import { Faq } from "./Faq";
import { FeatureColumns } from "./FeatureColumns";
import { FooterCta } from "./FooterCta";
import { Hero } from "./Hero";
import { ImageWithText } from "./ImageWithText";
import { LogoWall } from "./LogoWall";
import { PriceTable } from "./PriceTable";
import { ProductCarousel } from "./ProductCarousel";
import { ProductGrid } from "./ProductGrid";
import { RichText } from "./RichText";
import { Testimonials } from "./Testimonials";
import type { SectionComponent } from "./types";

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
};

export function sectionComponent(type: string): SectionComponent | null {
  return SECTION_COMPONENTS[type] ?? null;
}
