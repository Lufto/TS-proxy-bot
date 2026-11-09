import { getLocale } from 'next-intl/server';
import { getCommonData } from '@/app-layer/api/common';
import { Footer } from '@/widgets/footer';
import { Header } from '@/widgets/header';

export const Layout: FCClass = async ({
  children,
}) => {
  const locale = await getLocale();
  const res = await getCommonData(locale);
  const data = res ? res.data : undefined;

  return (
    <>
      {data?.header && <Header data={data.header} />}

      {children}

      {data?.footer && <Footer data={data.footer} />}
    </>
  );
};
