import SvgColor from 'src/components/svg-color';

// ----------------------------------------------------------------------

const icon = (name) => (
  <SvgColor src={`/assets/icons/navbar/${name}.svg`} sx={{ width: 1, height: 1 }} />
);

const navConfig = [
  {
    title: 'Статистика',
    path: '/',
    icon: icon('ic_analytics'),
  },
  {
    title: 'Мои улья',
    path: '/hives',
    icon: icon('ic_hives'),
  },
  
  //  {
    //  title: 'product',
    //  path: '/products',
    //  icon: icon('ic_cart'),
  //  },
  {
    title: 'Пользователи',
    path: '/user',
    icon: icon('ic_user'),
  },

  {
    title: 'Блог',
    path: '/blog',
    icon: icon('ic_blog'),
  },
  {
    title: 'login',
    path: '/login',
    icon: icon('ic_lock'),
  },
];

export default navConfig;
