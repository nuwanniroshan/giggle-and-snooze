export interface CommerceLocale {
  country: string;
  language: string;
}

export interface ShopConfig {
  localization: CommerceLocale & {
    locale: string;
  };
  search: {
    isEnabled: boolean;
  };
  site: {
    description: string;
    name: string;
    tagline: string;
    url: string;
  };
}
