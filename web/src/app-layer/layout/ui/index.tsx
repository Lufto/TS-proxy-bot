import { getLocale } from 'next-intl/server';
import { getCommonData } from '@/app-layer/api/common';
export const Layout: FCClass = async ({
  children,
}) => {
  const locale = await getLocale();
  const res = await getCommonData(locale);

  return (
    <>
      {res && res.data?.header && (
        <header>{res.data.header.title}</header>
      )}

      {children}

      {res && res.data?.footer && (
        <footer>{res.data.footer.title}</footer>
      )}
    </>
  );
};
