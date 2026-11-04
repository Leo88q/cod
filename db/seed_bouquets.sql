-- Демо-витрина букетов (агрегаторские ссылки Flowwow + локальные студии).
-- В проде каталог наполняется скрапером/API агрегаторов или вручную; здесь — демо-данные.

INSERT INTO bouquets (city, title, price_rub, photo_url, flowwow_url, shop_name, active) VALUES
('Москва', 'Букет «Розовая заря» — 25 роз', 3490, NULL, 'https://flowwow.com/moskva/bouquets/rose', 'Студия «Букетъ"', 1),
('Москва', 'Букет «Нежность» — пионы и эвкалипт', 4290, NULL, 'https://flowwow.com/moskva/bouquets/piony', 'Студия «Букетъ"', 1),
('Москва', 'Композиция «Солнечный» — подсолнухи', 2590, NULL, 'https://flowwow.com/moskva/bouquets/sun', 'FloMarket', 1),
('Москва', 'Букет «Классика» — 51 красная роза', 5990, NULL, 'https://flowwow.com/moskva/bouquets/rose51', 'Русский Букет', 1),
('Санкт-Петербург', 'Букет «Питерская осень» — хризантемы', 2990, NULL, 'https://flowwow.com/spb/bouquets/autumn', 'Букет Питер', 1),
('Санкт-Петербург', 'Композиция «Белая ночь» — белые розы', 4590, NULL, 'https://flowwow.com/spb/bouquets/white', 'Flor2U', 1),
('Санкт-Петербург', 'Букет «Для неё» — пионовидные розы', 5490, NULL, 'https://flowwow.com/spb/bouquets/peony', 'Букет Питер', 1),
('Москва', 'Букет «С днём рождения!» — разнотравье', 2290, NULL, 'https://flowwow.com/moskva/bouquets/birthday', 'Студия «Букетъ"', 1);
